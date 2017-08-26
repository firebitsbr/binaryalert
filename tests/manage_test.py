"""Unit tests for the manage.py CLI script."""
# pylint: disable=no-self-use,protected-access
import base64
import os
import sys
from unittest import mock, TestCase

import boto3
import moto
from pyfakefs import fake_filesystem_unittest

import manage


def _mock_input(prompt: str) -> str:
    """Mock for the user input() function to automatically respond with valid answers."""
    if prompt.startswith('AWS Region'):
        return 'us-west-2'
    elif prompt.startswith('Unique name prefix'):
        return ' NEW_NAME_PREFIX '  # Spaces and case shouldn't matter.
    elif prompt.startswith('Enable the CarbonBlack downloader'):
        return 'yes'
    elif prompt.startswith('CarbonBlack URL'):
        return 'https://new-example.com'
    elif prompt.startswith('Change the CarbonBlack API token'):
        return 'yes'
    else:
        raise NotImplementedError('Unexpected input prompt ' + prompt)


@mock.patch('sys.stderr', mock.MagicMock())  # pyhcl complains about unused tokens to stderr
class BinaryAlertConfigTestFakeFilesystem(fake_filesystem_unittest.TestCase):
    """Tests of the BinaryAlertConfig class that use a fake filesystem."""

    def setUp(self):
        self.setUpPyfakefs()

        # pyhcl automatically writes "parsetab.dat"  in its site-package path.
        for path in sys.path:
            if path.endswith('site-packages'):
                self.fs.MakeDirectories(os.path.join(path, 'hcl'))

        # Create variables.tf file.
        self.fs.CreateFile(
            manage.VARIABLES_FILE,
            contents='\n'.join([
                'variable "aws_region" {}',
                'variable "name_prefix" {}',
                'variable "enable_carbon_black_downloader" {}',
                'variable "carbon_black_url" {}',
                'variable "encrypted_carbon_black_api_token" {}'
            ])
        )

        # Create terraform.tfvars file.
        self.fs.CreateFile(
            manage.CONFIG_FILE,
            contents='\n'.join([
                '// comment1',
                'aws_region = "us-east-1" // comment2',
                'name_prefix = "name_prefix" // comment3',
                'enable_carbon_black_downloader = 1 // comment4',
                'carbon_black_url = "https://example.com" // comment5',
                'encrypted_carbon_black_api_token = "{}"'.format('A' * 100)
            ])
        )

    def test_property_accesses(self):
        """Access each property in the BinaryAlertConfig."""
        config = manage.BinaryAlertConfig()

        self.assertEqual('us-east-1', config.aws_region)
        self.assertEqual('name_prefix', config.name_prefix)
        self.assertEqual(1, config.enable_carbon_black_downloader)
        self.assertEqual('https://example.com', config.carbon_black_url)
        self.assertEqual('A' * 100, config.encrypted_carbon_black_api_token)
        self.assertEqual('name_prefix_binaryalert_batcher', config.binaryalert_batcher_name)
        self.assertEqual('name.prefix.binaryalert-binaries.us-east-1',
                         config.binaryalert_s3_bucket_name)

    def test_variable_not_defined(self):
        """InvalidConfigError is raised if a variable declaration is missing."""
        with open(manage.CONFIG_FILE, 'w') as config_file:
            config_file.write('aws_region = "us-east-1"\n')

        with self.assertRaises(manage.InvalidConfigError):
            manage.BinaryAlertConfig()

    def test_invalid_aws_region(self):
        """InvalidConfigError raised if AWS region is set incorrectly."""
        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.aws_region = 'invalid-region'

    def test_invalid_name_prefix(self):
        """InvalidConfigError raised if name prefix is blank."""
        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.name_prefix = ""

    def test_invalid_enable_carbon_black_downloader(self):
        """InvalidConfigError raised if enable_downloader is not an int."""
        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.enable_carbon_black_downloader = '1'

    def test_invalid_carbon_black_url(self):
        """InvalidConfigError raised if URL doesn't start with http(s)."""
        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.carbon_black_url = 'example.com'

    def test_invalid_encrypted_carbon_black_api_token(self):
        """InvalidConfigError raised if encrypted token is too short."""
        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.encrypted_carbon_black_api_token = 'ABCD'

    @mock.patch('manage.input', side_effect=_mock_input)
    @mock.patch.object(manage.BinaryAlertConfig, '_encrypt_cb_api_token')
    def test_configure_with_defaults(
            self, mock_encrypt: mock.MagicMock, mock_input: mock.MagicMock):
        """Test configure() when all variables have already had set values."""
        config = manage.BinaryAlertConfig()
        config.configure()

        # Verify the mock calls.
        mock_encrypt.assert_called_once()
        mock_input.assert_has_calls([
            mock.call('AWS Region (us-east-1):'),
            mock.call('Unique name prefix, e.g. "company_team" (name_prefix):'),
            mock.call('Enable the CarbonBlack downloader [yes/no]? (yes):'),
            mock.call('CarbonBlack URL (https://example.com):'),
            mock.call('Change the CarbonBlack API token [yes/no]? (no):')
        ])

        # Verify that the configuration has changed.
        self.assertEqual('us-west-2', config.aws_region)
        self.assertEqual('new_name_prefix', config.name_prefix)
        self.assertEqual(1, config.enable_carbon_black_downloader)

    @mock.patch('manage.input', side_effect=_mock_input)
    @mock.patch.object(manage.BinaryAlertConfig, '_encrypt_cb_api_token')
    def test_configure_with_no_defaults(
            self, mock_encrypt: mock.MagicMock, mock_input: mock.MagicMock):
        """Test configure() without any values set - no defaults should print."""
        with open(manage.CONFIG_FILE, 'w') as config_file:
            config_file.write('\n'.join([
                'aws_region = ""',
                'name_prefix = ""',
                'enable_carbon_black_downloader = 0',
                'carbon_black_url = ""',
                'encrypted_carbon_black_api_token = ""'
            ]))

        config = manage.BinaryAlertConfig()
        config.configure()

        # Verify the mock calls.
        mock_encrypt.assert_called_once()
        mock_input.assert_has_calls([
            mock.call('AWS Region:'),
            mock.call('Unique name prefix, e.g. "company_team":'),
            mock.call('Enable the CarbonBlack downloader [yes/no]? (no):'),
            mock.call('CarbonBlack URL:'),
        ])

    def test_validate_valid_with_downloader(self):
        """Test validate() with all values set correctly."""
        config = manage.BinaryAlertConfig()
        config.validate()

        # None of the instance properties should have changed.
        self.test_property_accesses()

    def test_validate_valid_without_downloader(self):
        """Test validate() without any CarbonBlack values set - still valid."""
        with open(manage.CONFIG_FILE, 'w') as config_file:
            config_file.write('\n'.join([
                'aws_region = "us-east-1"',
                'name_prefix = "name_prefix"',
                'enable_carbon_black_downloader = 0',
                'carbon_black_url = ""',
                'encrypted_carbon_black_api_token = ""',
            ]))

        config = manage.BinaryAlertConfig()
        config.validate()

    def test_validate_invalid(self):
        """Test validate() with an invalid configuration file."""
        with open(manage.CONFIG_FILE, 'w') as config_file:
            config_file.write('\n'.join([
                'aws_region = "BAD_REGION"',
                'name_prefix = "name_prefix"',
                'enable_carbon_black_downloader = 0',
                'carbon_black_url = ""',
                'encrypted_carbon_black_api_token = ""',
            ]))

        config = manage.BinaryAlertConfig()
        with self.assertRaises(manage.InvalidConfigError):
            config.validate()

    def test_save(self):
        """New configuration is successfully written and comments are preserved."""
        config = manage.BinaryAlertConfig()
        config.aws_region = 'us-west-2'
        config.name_prefix = 'new_name_prefix'
        config.enable_carbon_black_downloader = 0
        config.carbon_black_url = 'https://example2.com'
        config.encrypted_carbon_black_api_token = 'B' * 100
        config.save()

        # Verify that all of the original comments were preserved.
        with open(manage.CONFIG_FILE) as config_file:
            raw_data = config_file.read()
            for i in range(1, 6):
                self.assertIn('comment{}'.format(i), raw_data)

        new_config = manage.BinaryAlertConfig()
        self.assertEqual(config.aws_region, new_config.aws_region)
        self.assertEqual(config.name_prefix, new_config.name_prefix)
        self.assertEqual(
            config.enable_carbon_black_downloader, new_config.enable_carbon_black_downloader)
        self.assertEqual(
            config.encrypted_carbon_black_api_token, new_config.encrypted_carbon_black_api_token)


class BinaryAlertConfigTestRealFilesystem(TestCase):
    """Tests of the BinaryAlertConfig class that use a real filesystem."""

    @moto.mock_kms
    @mock.patch('getpass.getpass', return_value='abcd' * 10)
    @mock.patch('manage.print')
    @mock.patch('subprocess.check_call')
    def test_encrypt_cb_api_token(
            self, mock_subprocess: mock.MagicMock, mock_print: mock.MagicMock,
            mock_getpass: mock.MagicMock):
        """Verify that token encryption is done correctly."""
        config = manage.BinaryAlertConfig()
        config._encrypt_cb_api_token()

        # Verify that the mocks were called as expected.
        mock_getpass.assert_called_once()
        mock_print.assert_has_calls([
            mock.call('Terraforming KMS key...'),
            mock.call('Encrypting API token...')
        ])
        mock_subprocess.assert_called_once()

        # Decrypting the key should result in the original value.
        plaintext_api_key = boto3.client('kms').decrypt(
            CiphertextBlob=base64.b64decode(config.encrypted_carbon_black_api_token)
        )['Plaintext'].decode('ascii')
        self.assertEqual(mock_getpass.return_value, plaintext_api_key)
