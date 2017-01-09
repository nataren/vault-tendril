"""The tendril class"""

#from __future__ import print_function
#import logging
import getpass
import os
import sys
import json
import select
import datetime
import hashlib
import tempfile
from subprocess import call
from time import sleep
import yaml
import requests
def valid_path(path):
    """Makes a best guess at detecting whether a path is okay to try to write to"""
    last = path.split('/')[-1]
    try:
        int(last)
        return False
    except ValueError:
        return True

def get_raw_data(use_editor=True):
    """
    Figure out the best way to get the data from the user. Use STDIN if it's
    available, otherwise open up an editor.
    """
    sleep(0.05) # Gah this is annoying, figure out how to do this right
    if select.select([sys.stdin,], [], [], 0.0)[0]:
        raw_data = sys.stdin.read()
    else:
        raw_data = None
    editor = os.environ.get('EDITOR', 'vi')

    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as temp_file:
        temp_file.write(raw_data or "")
        temp_file.flush()
    if use_editor:
        call([editor, temp_file.name], stdin=open('/dev/tty', 'r'))

    with open(temp_file.name, 'r') as temp_file:
        raw_data = temp_file.read()
    try:
        data = json.loads(raw_data)
    except ValueError:
        try:
            data = yaml.load(raw_data)
        except yaml.scanner.ScannerError:
            return False, "Data is neither JSON nor YAML"
        except yaml.parser.ParserError:
            return False, "Data is neither JSON nor YAML"
    if data is None:
        return False, "No data provided"
    return True, data

def create_history(data, next_version):
    """Generate the history object"""
    hash_object = hashlib.sha256()
    hash_object.update(json.dumps(data))
    digest = hash_object.hexdigest()
    history = {"user":getpass.getuser(),
               "date":"%s UTC" % datetime.datetime.utcnow(),
               "action":"created",
               "digest":digest,
               "version":next_version
              }
    return history, digest


class Tendril(object):
    """This class does all of the heavy lifting for tendril, including
    the raw reads and writes to the vault database and the logic for read /
    write / list."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-arguments

    def __init__(self,
                 vault_token=None,
                 vault_addr='http://localhost:8200',
                 vault_prefix='config',
                 vault_cert_path=None,
                 use_socks=False,
                 socks_addr=None,
                 output_format=None,
                 force=False,
                 use_editor=True
                ):
        self.vault_addr = vault_addr
        self.vault_prefix = vault_prefix
        self.vault_cert_path = vault_cert_path
        if vault_cert_path is not None:
            self.vault_cert_path = os.path.expanduser(vault_cert_path)
        if use_socks == "yes":
            self.proxies = {
                'http': socks_addr,
                'https': socks_addr
            }
        else:
            self.proxies = None
        self.use_socks = use_socks
        self.output_format = output_format
        self.headers = {'X-Vault-Token': vault_token}
        self.force = False
        if force == 'True':
            self.force = True
        self.use_editor = True
        if use_editor == 'False':
            self.use_editor = False
    def _write_data(self, path, data):
        try:
            response = requests.post(
                '%s/v1/%s/%s' % (self.vault_addr, self.vault_prefix, path),
                json=data, headers=self.headers)
        except requests.exceptions.ConnectionError, error:
            return False, str(error)
        if response.status_code == 204:
            return True, None
        elif response.status_code == 400:
            return False, "Permission denied"
        return False, "Unknown error"

    def _read_data(self, path):
        try:
            response = requests.get(
                '%s/v1/%s/%s' % (self.vault_addr, self.vault_prefix, path),
                headers=self.headers, verify=self.vault_cert_path)
        except requests.exceptions.ConnectionError, error:
            return False, str(error)
        if response.status_code == 200:
            if 'data' in response.json():
                return True, response.json()['data']
            else:
                return False, "No data returned"
        elif response.status_code == 404:
            return False, "No data found at %s" % path
        elif response.status_code == 400:
            return False, "Permission denied"
        return False, "Unknown error"

    def _list_path(self, path):

        # pylint: disable=too-many-return-statements

        try:
            response = requests.get(
                '%s/v1/%s/%s?list=true' % (self.vault_addr, self.vault_prefix, path),
                headers=self.headers, verify=self.vault_cert_path, proxies=self.proxies)
        except requests.exceptions.ConnectionError, error:
            return False, str(error)
        if response.status_code == 200:
            if 'data' in response.json():
                if 'keys' in response.json()['data']:
                    return True, response.json()['data']['keys']
                else:
                    return False, "No keys in data"
            else:
                return False, "No data in response"
        elif response.status_code == 404:
            if path == '':
                return False, 'No keys found'
            return False, "No keys found at %s" % path
        elif response.status_code == 400:
            return False, "Permission denied"
        return False, "Unknown error"

    def list(self, path):
        """Given a path this will list the next available branches in the path,
        or if the path has versions available (i.e. the path has no sub-paths
        except for versions) then it will show the available versions along
        with available metadata"""
        path = path.lstrip('/').rstrip('/')
        (success, response) = self._list_path(path)
        if not success:
            return False, "No metadata found"
        return_text = ''
        if '__metadata' in response:
            (success, metadata) = self._read_data('%s/__metadata' % (path))
            if success:
                history = {}
                for item in metadata['history']:
                    if item['action'] == 'created':
                        history[item['version']] = {}
                        history[item['version']]['date'] = item['date']
                        history[item['version']]['user'] = item['user']
                for version in metadata['versions']:
                    if 'current' in metadata and version == metadata['current']:

                        return_text += "%s by %s on %s (current)" % (
                            version,
                            history[version]['user'],
                            history[version]['date'])
                    else:
                        return_text += "%s by %s on %s" % (
                            version,
                            history[version]['user'],
                            history[version]['date'])
        else:
            for k in response:
                if path == '':
                    return_text += k
                else:
                    return_text += "%s/%s" % (path, k)
        return True, return_text

    def history(self, path):
        """Given a path this will list the history of the path, including user,
        date, and action (created, promoted).
        """
        path = path.lstrip('/').rstrip('/')
        (success, response) = self._list_path(path)
        if not success:
            return False, "No metadata found"
        if '__metadata' in response:
            (success, metadata) = self._read_data('%s/__metadata' % (path))
            return_text = ''
            if success:
                if self.output_format == 'json':
                    return_text = json.dumps(metadata['history'], indent=2)
                else:
                    max_width = 0
                    for item in metadata['history']:
                        if len(str(item['version'])) > max_width:
                            max_width = len(str(item['version']))
                    for item in metadata['history']:
                        return_text += "{} version {: >{}} {:<8} by {}".format(
                            item['date'],
                            int(item['version']),
                            max_width,
                            item['action'],
                            item['user']
                        )
        return True, return_text

    def _get_metadata(self, path, next_version):
        (success, metadata) = self._read_data('%s/__metadata' % path)
        if success:
            if next_version is None:
                next_version = int(metadata['versions'][-1]) + 1
        else:
            if next_version is None:
                next_version = 1
            metadata = None
        return metadata, next_version



    def write(self, path):
        """Given a path this will write the raw_data (either passed in or from
        STDIN) to vault, computing the appropriate version."""
        path = path.lstrip('/').rstrip('/')
        next_version = None
        if not valid_path(path):
            if self.force:
                next_version = int(path.split('/')[-1])
                path = '/'.join(path.split('/')[0:-1])
                if not valid_path(path):
                    return False, "This is not a valid path"
            else:
                return False, "Cannot save to a specific version, use --force to override"

        (metadata, next_version) = self._get_metadata(path, next_version)
        success, data = get_raw_data(self.use_editor)
        if not success:
            return False, data
        history, digest = create_history(data, next_version)
        if not self.force and metadata is not None and digest in metadata['digests']:
            conflicting_version = 0
            for index, t_digest in enumerate(metadata['digests']):
                if digest == t_digest:
                    conflicting_version = metadata['versions'][index]
            return False, "This configuration is identical to version %s." % conflicting_version
        if metadata is not None:
            try:
                index = metadata['versions'].index(next_version)
            except ValueError:
                index = None
            metadata['history'].append(history)
            if index is None:
                metadata['versions'].append(next_version)
                metadata['digests'].append(digest)
            else:
                metadata['digests'][index] = digest
        else:
            metadata = {"current":None, "versions":[next_version],
                        "history":[history], "digests":[digest]
                       }
        (success, response) = self._write_data('%s/%s' % (path, next_version), data)
        if success:
            (success, response) = self._write_data('%s/%s' % (path, '__metadata'), metadata)
        if success:
            response = "{} version {} {} by {}".format(history['date'],
                                                       int(history['version']),
                                                       history['action'],
                                                       history['user']
                                                      )

        return success, response

    def read(self, full_path):
        """Given a path this will read the key/value pairs from vault and
        present them to the user in the desired format. If the path does not
        have a version, the current version will be pulled from vault and used.
        If there is no current version, a list of available options will be
        provided instead of the key/value pairs."""
        full_path = full_path.lstrip('/').rstrip('/')
        return_text = ''
        try:
            version = full_path.split('/')[-1]
            path = '/'.join(full_path.split('/')[:-1])
            int(version)
            (success, response) = self._read_data('%s/%s' % (path, version))
        except ValueError:
            (success, response) = self._read_data('%s/__metadata' % full_path)
            if 'current' in response and response['current'] is not None:
                version = response['current']
                (success, response) = self._read_data('%s/%s' % (full_path, version))
            else:
                if isinstance(response, dict):
                    if 'versions' in response:
                        return_text += "Available versions are: "
                        for version in response['versions']:
                            return_text += "%s " % str(version)
                        success = False
                        response = return_text
        if success:
            if self.output_format == 'export':
                for key in sorted(response):
                    return_text += "export %s=\"%s\"" % (key, response[key])
            elif self.output_format == 'json':
                return_text += json.dumps(response, indent=2)
            elif self.output_format == 'yaml':
                return_text += '---'
                return_text += yaml.safe_dump(response, default_flow_style=False),
            return True, return_text
        return success, response

    def promote(self, full_path):
        """This will promote a given path to mark it as 'current'. The path must
        end with a version number or an errro will be returned."""
        full_path = full_path.lstrip('/').rstrip('/')

        version = full_path.split('/')[-1]
        path = '/'.join(full_path.split('/')[:-1])
        (success, metadata) = self._read_data('%s/__metadata' % path)
        if success:
            if int(version) in metadata['versions']:
                if int(version) != metadata['current']:
                    metadata['current'] = int(version)
                    history = {"user":getpass.getuser(),
                               "date":"%s UTC" % datetime.datetime.utcnow(),
                               "action":"promoted",
                               "version":version
                              }
                    metadata['history'].append(history)
                    (success, response) = self._write_data('%s/__metadata' % path, metadata)
                    if success:
                        response = "{} version {} {} by {}".format(history['date'],
                                                                   int(history['version']),
                                                                   history['action'],
                                                                   history['user']
                                                                  )

                    return success, response
                else:
                    return False, "%s is already current" % version
            else:
                return False, "%s is not in %s" % (version, metadata['versions'])
        else:
            return False, "Error: %s" % metadata
