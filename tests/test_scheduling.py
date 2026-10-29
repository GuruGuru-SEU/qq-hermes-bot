"""Use native Hermes job storage, scripts, monitor gating and repeat exhaustion."""
import json
import os
import subprocess
import unittest
from unittest.mock import patch

import test_personal
from qqhermes.scheduling import prepare_reminder


class SchedulingTests(unittest.TestCase):
    setUp=test_personal.PersonalTests.setUp
    tearDown=test_personal.PersonalTests.tearDown

    def call(self,**args):
        from tools.cronjob_tools import _cronjob_handler
        result=json.loads(_cronjob_handler(args))
        self.assertTrue(result['success'],result)
        return result

    def test_literal_script_limited_repeat_and_natural_management(self):
        from cron.jobs import get_job,list_jobs,mark_job_run
        from cron.scheduler import run_job
        text="喝杯水 👀\n'\"; $(touch /tmp/should-not-exist)\nimport os; raise Exception('not code')"
        source=self.profile/'reminder.txt';source.write_text(text)
        prepared=prepare_reminder(source)
        self.assertFalse(prepared['scheduled'])
        self.assertEqual(list_jobs(include_disabled=True),[])
        result=self.call(action='create',name='喝水提醒',schedule='every 1m',repeat=3,
                         script=prepared['script'],no_agent=True,prompt='固定提醒',
                         deliver='origin',failure_deliver='local',paused=True)
        job_id=result['job_id'];job=get_job(job_id)
        self.assertEqual(job['origin']['chat_id'],'dm-42')
        self.assertEqual(job['repeat']['times'],3)
        # Native script execution returns the exact literal output without constructing an agent.
        with patch('cron.scheduler._construct_cron_agent',side_effect=AssertionError('LLM used')):
            success,doc,output,error=run_job(job)
        self.assertTrue(success,error);self.assertEqual(output,text)
        self.call(action='update',job_id='喝水提醒',schedule='every 2m')
        updated=get_job(job_id)
        self.assertEqual(updated['script'],prepared['script'])
        self.assertEqual(updated['repeat']['times'],3)
        self.call(action='resume',job_id='喝水提醒')
        self.assertTrue(get_job(job_id)['enabled'])
        self.call(action='pause',job_id='喝水提醒')
        self.assertFalse(get_job(job_id)['enabled'])
        self.call(action='resume',job_id=job_id)
        for _ in range(3):self.assertTrue(mark_job_run(job_id,True))
        completed=get_job(job_id)
        self.assertFalse(completed['enabled'])
        self.assertEqual(completed['repeat']['completed'],3)
        self.call(action='remove',job_id=job_id)
        self.assertIsNone(get_job(job_id))

    def test_monitor_baseline_unchanged_changed_and_source_failure(self):
        from cron.jobs import get_job
        from cron.scheduler import _apply_monitor_gate
        scripts=self.profile/'scripts';scripts.mkdir(exist_ok=True)
        source=scripts/'watch.py';source.write_text("print('version 1')\n")
        result=self.call(action='create',name='更新监控',schedule='every 1h',prompt='只报告更新',
                         monitor='watch.py',continuity=True,attach_to_session=True,
                         deliver='origin',failure_deliver='local',paused=True)
        job_id=result['job_id']
        def tick():return _apply_monitor_gate(get_job(job_id),job_id,'更新监控',None)
        early,_,context=tick()
        self.assertIsNone(early);self.assertIn('Baseline',context)
        early,_,context=tick()
        self.assertTrue(early[0]);self.assertEqual(early[2],'[SILENT]')
        self.assertIsNone(context)
        source.write_text("print('version 2')\n")
        early,_,context=tick()
        self.assertIsNone(early);self.assertIn('version 2',context)
        job=get_job(job_id);saved_state=job['monitor_state']
        self.assertIn('self',job['context_from']);self.assertTrue(job['attach_to_session'])
        source.write_text("raise RuntimeError('synthetic source failure')\n")
        early,_,context=tick()
        self.assertFalse(early[0]);self.assertIn('synthetic source failure',early[3])
        self.assertEqual(get_job(job_id)['monitor_state'],saved_state)

    def test_one_shot_and_invalid_reminder_inputs(self):
        from cron.jobs import get_job
        result=self.call(action='create',name='一次性',prompt='hello',schedule='in 30m',paused=True)
        job=get_job(result['job_id'])
        self.assertEqual(job['repeat']['times'],1)
        self.assertEqual(job['schedule']['kind'],'once')
        for text in ('', 'x'*4001, '汉'*6000):
            source=self.profile/'bad.txt';source.write_text(text)
            with self.assertRaises(ValueError):prepare_reminder(source)
        other=self.home/'profiles/qq-dm-43/text.txt';other.write_text('private')
        with self.assertRaises(ValueError):prepare_reminder(other)
        link=self.profile/'link.txt';link.symlink_to(other)
        with self.assertRaises(ValueError):prepare_reminder(link)

    def test_installed_cli_works_without_terminal_pythonpath(self):
        source=self.profile/'text.txt';source.write_text('准时提醒。')
        env=dict(os.environ,HERMES_HOME=str(self.profile),PYTHONPATH='')
        result=subprocess.run(['/usr/local/bin/qq-reminder','--text-file',str(source)],
                              env=env,text=True,capture_output=True,check=True)
        prepared=json.loads(result.stdout)
        self.assertFalse(prepared['scheduled'])
        output=subprocess.check_output(['/opt/hermes/.venv/bin/python',str(self.profile/'scripts'/prepared['script'])],text=True)
        self.assertEqual(output,'准时提醒。')

    def test_builtin_upgrade_preserves_custom_skills_jobs_and_previous_entrypoint(self):
        from cron.jobs import list_jobs
        self.call(action='create',name='keep me',prompt='hello',schedule='in 1h',paused=True)
        jobs=list_jobs(include_disabled=True)
        entry=self.profile/'skills/qq-scheduling/SKILL.md';entry.write_text('old scheduling entrypoint')
        custom=self.profile/'skills/custom';custom.mkdir();(custom/'SKILL.md').write_text('custom skill')
        subprocess.run(['/opt/hermes/.venv/bin/python','/opt/qq-hermes-bot/deploy/bootstrap.py'],check=True,capture_output=True)
        self.assertIn('qq-scheduling',entry.read_text())
        backups=list((self.profile/'backups').glob('qq-scheduling-skill-*/SKILL.md'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(),'old scheduling entrypoint')
        self.assertEqual((custom/'SKILL.md').read_text(),'custom skill')
        self.assertEqual(list_jobs(include_disabled=True),jobs)
