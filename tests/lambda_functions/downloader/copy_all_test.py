"""Unit tests for downloader/copy_all.py script."""
# pylint: disable=no-self-use
import multiprocessing
from typing import Any, Dict, List
import unittest
from unittest import mock

NUM_BINARIES = 100  # Number of binaries returned by the mock CarbonBlack.


class MockBinary(object):
    """Mock CarbonBlack Binary instance."""
    def __init__(self, index: int):
        self.md5 = 'test-md5-{}'.format(index)


class MockBinarySelect(object):
    """Mock response from CarbonBlack.Select()."""
    def all(self) -> List[MockBinary]:
        return [MockBinary(i) for i in range(NUM_BINARIES)]


class MockCarbonBlack(object):
    """Mock CarbonBlack api object."""
    def select(self, _) -> MockBinarySelect:
        return MockBinarySelect()


class MockMain(object):
    """Mock out the downloader Lambda main.py."""
    CARBON_BLACK = MockCarbonBlack()

    def __init__(self, inject_errors: bool=False):
        self.inject_errors = inject_errors
        # Record all download invocations across all Consumer processes.
        self.download_invocations = multiprocessing.Array('i', range(100))
        self.index = multiprocessing.Value('i', 0)

    def download_lambda_handler(self, event: Dict[str, Any], _):
        """Record requests to the downloader function."""
        with self.index.get_lock():
            self.download_invocations[self.index.value] = int(event['md5'].split('-')[2])
            self.index.value += 1
        if self.inject_errors:
            raise FileNotFoundError


@mock.patch('lambda_functions.downloader')
@mock.patch('logging.getLogger')
class CopyAllTest(unittest.TestCase):
    """Test multiprocess producer-consumer queue for mocked-out tasks."""

    def test_copy_all_binaries(self, mock_logger: mock.MagicMock, mock_downloader: mock.MagicMock):
        """Test the top-level copy function with real multiprocessing.

        Note that coverage doesn't see code run by other processes, so it doesn't show any coverage
        for the CopyTask or Consumer classes, but we do in fact run all of the code.
        """
        mock_main = MockMain()
        mock_downloader.main = mock_main

        from lambda_functions.downloader import copy_all
        copy_all.copy_all_binaries()

        # Verify that every binary "in CarbonBlack" was sent to a Consumer process.
        self.assertEqual(NUM_BINARIES, mock_main.index.value)
        md5s = [mock_main.download_invocations[i] for i in range(NUM_BINARIES)]
        self.assertEqual(list(range(NUM_BINARIES)), sorted(md5s))

        # Verify that a logger was created for the main process and for each consumer.
        # Also verify each log statement from the main logger.
        mock_logger.assert_has_calls(
            [mock.call('carbon_black_copy')] +
            [mock.call().debug('Enqueuing %s', mock.ANY)] * NUM_BINARIES +
            [mock.call('Consumer-{}'.format(i)) for i in range(1, copy_all.NUM_CONSUMERS)] +
            [mock.call().info('All CopyTasks Finished!')],
            any_order=True
        )

    def test_copy_with_errors(self, mock_logger: mock.MagicMock, mock_downloader: mock.MagicMock):
        """Test top-level copy function with injected errors."""
        mock_main = MockMain(inject_errors=True)
        mock_downloader.main = mock_main

        from lambda_functions.downloader import copy_all
        copy_all.copy_all_binaries()

        # Verify that the root logger logged all of the failed binaries.
        mock_logger.assert_has_calls([
            mock.call().error('%d %s failed to copy: \n%s', NUM_BINARIES, 'binaries', mock.ANY)
        ])
