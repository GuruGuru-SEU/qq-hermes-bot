"""Private Hermes integration, contract-tested against the pinned image."""


async def interrupt_turn(adapter, runner, event):
    with runner._media_delivery_scope_for_source(event.source):
        from gateway.run import _INTERRUPT_REASON_STOP
        key=runner._session_key_for_source(event.source)
        generation=runner._interrupt_running_turn(key,interrupt_reason=_INTERRUPT_REASON_STOP,
                                                 invalidation_reason='qq_stop_current')
        await adapter.interrupt_session_activity(key,event.source.chat_id)
        await adapter.cancel_session_processing(key,release_guard=False,discard_pending=False)
        runner._drop_turn_slot(key,run_generation=generation)
        adapter._release_session_guard(key)
