import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.resolver import ThreadedResolver
from qqhermes.media import PublicResolver, ImageDNSRejected
from qqhermes.settings import fake_ip_networks


class ProxyDNSTests(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, addresses, ranges):
        with patch.object(ThreadedResolver, 'resolve', AsyncMock(return_value=[
                {'host': address} for address in addresses])), patch(
                'qqhermes.media.fake_ip_networks', return_value=fake_ip_networks(ranges)):
            resolver = PublicResolver()
            try:
                return await resolver.resolve('gchat.qpic.cn', 443)
            finally:
                await resolver.close()

    async def test_only_declared_proxy_sentinels_are_accepted(self):
        with self.assertRaises(ImageDNSRejected):
            await self.resolve(['198.18.0.33'], [])
        result = await self.resolve(['198.18.0.33'], ['198.18.0.1/16'])
        self.assertEqual(result[0]['host'], '198.18.0.33')
        self.assertTrue(await self.resolve(['1.1.1.1'], []))

    async def test_proxy_exception_never_allows_private_or_mixed_answers(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254',
                        '::ffff:127.0.0.1', '224.0.0.1', '198.19.0.1'):
            with self.subTest(address=address), self.assertRaises(ImageDNSRejected):
                await self.resolve(['198.18.0.33', address], ['198.18.0.0/16'])

    def test_configuration_cannot_exempt_lan_or_entire_internet(self):
        for entry in ('0.0.0.0/0', '10.0.0.0/8', '::/0', 'invalid'):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                fake_ip_networks([entry])


if __name__ == '__main__':
    unittest.main()
