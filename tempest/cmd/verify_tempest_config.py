#!/usr/bin/env python

# Copyright 2013 IBM Corp.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import argparse
import json
import os
import sys
import urlparse

import httplib2
from six.moves import configparser

from tempest import clients
from tempest import config


CONF = config.CONF
RAW_HTTP = httplib2.Http()
CONF_FILE = None
OUTFILE = sys.stdout


def _get_config_file():
    default_config_dir = os.path.join(os.path.abspath(
        os.path.dirname(os.path.dirname(__file__))), "etc")
    default_config_file = "tempest.conf"

    conf_dir = os.environ.get('TEMPEST_CONFIG_DIR', default_config_dir)
    conf_file = os.environ.get('TEMPEST_CONFIG', default_config_file)
    path = os.path.join(conf_dir, conf_file)
    fd = open(path, 'rw')
    return fd


def change_option(option, group, value):
    config_parse = configparser.SafeConfigParser()
    config_parse.optionxform = str
    config_parse.readfp(CONF_FILE)
    if not config_parse.has_section(group):
        config_parse.add_section(group)
    config_parse.set(group, option, str(value))
    global OUTFILE
    config_parse.write(OUTFILE)


def print_and_or_update(option, group, value, update):
    print('Config option %s in group %s should be changed to: %s'
          % (option, group, value))
    if update:
        change_option(option, group, value)


def verify_glance_api_versions(os, update):
    # Check glance api versions
    __, versions = os.image_client.get_versions()
    if CONF.image_feature_enabled.api_v1 != ('v1.1' in versions or 'v1.0' in
                                             versions):
        print_and_or_update('api_v1', 'image_feature_enabled',
                            not CONF.image_feature_enabled.api_v1, update)
    if CONF.image_feature_enabled.api_v2 != ('v2.0' in versions):
        print_and_or_update('api_v2', 'image_feature_enabled',
                            not CONF.image_feature_enabled.api_v2, update)


def _get_unversioned_endpoint(base_url):
    endpoint_parts = urlparse.urlparse(base_url)
    endpoint = endpoint_parts.scheme + '://' + endpoint_parts.netloc
    return endpoint


def _get_api_versions(os, service):
    client_dict = {
        'nova': os.servers_client,
        'keystone': os.identity_client,
        'cinder': os.volumes_client,
    }
    client_dict[service].skip_path()
    endpoint = _get_unversioned_endpoint(client_dict[service].base_url)
    __, body = RAW_HTTP.request(endpoint, 'GET')
    client_dict[service].reset_path()
    body = json.loads(body)
    if service == 'keystone':
        versions = map(lambda x: x['id'], body['versions']['values'])
    else:
        versions = map(lambda x: x['id'], body['versions'])
    return versions


def verify_keystone_api_versions(os, update):
    # Check keystone api versions
    versions = _get_api_versions(os, 'keystone')
    if CONF.identity_feature_enabled.api_v2 != ('v2.0' in versions):
        print_and_or_update('api_v2', 'identity_feature_enabled',
                            not CONF.identity_feature_enabled.api_v2, update)
    if CONF.identity_feature_enabled.api_v3 != ('v3.0' in versions):
        print_and_or_update('api_v3', 'identity_feature_enabled',
                            not CONF.identity_feature_enabled.api_v3, update)


def verify_nova_api_versions(os, update):
    versions = _get_api_versions(os, 'nova')
    if CONF.compute_feature_enabled.api_v3 != ('v3.0' in versions):
        print_and_or_update('api_v3', 'compute_feature_enabled',
                            not CONF.compute_feature_enabled.api_v3, update)


def verify_cinder_api_versions(os, update):
    # Check cinder api versions
    versions = _get_api_versions(os, 'cinder')
    if CONF.volume_feature_enabled.api_v1 != ('v1.0' in versions):
        print_and_or_update('api_v1', 'volume_feature_enabled',
                            not CONF.volume_feature_enabled.api_v1, update)
    if CONF.volume_feature_enabled.api_v2 != ('v2.0' in versions):
        print_and_or_update('api_v2', 'volume_feature_enabled',
                            not CONF.volume_feature_enabled.api_v2, update)


def get_extension_client(os, service):
    extensions_client = {
        'nova': os.extensions_client,
        'nova_v3': os.extensions_v3_client,
        'cinder': os.volumes_extension_client,
        'neutron': os.network_client,
        'swift': os.account_client,
    }
    if service not in extensions_client:
        print('No tempest extensions client for %s' % service)
        exit(1)
    return extensions_client[service]


def get_enabled_extensions(service):
    extensions_options = {
        'nova': CONF.compute_feature_enabled.api_extensions,
        'nova_v3': CONF.compute_feature_enabled.api_v3_extensions,
        'cinder': CONF.volume_feature_enabled.api_extensions,
        'neutron': CONF.network_feature_enabled.api_extensions,
        'swift': CONF.object_storage_feature_enabled.discoverable_apis,
    }
    if service not in extensions_options:
        print('No supported extensions list option for %s' % service)
        exit(1)
    return extensions_options[service]


def verify_extensions(os, service, results):
    extensions_client = get_extension_client(os, service)
    __, resp = extensions_client.list_extensions()
    if isinstance(resp, dict):
        # Neutron's extension 'name' field has is not a single word (it has
        # spaces in the string) Since that can't be used for list option the
        # api_extension option in the network-feature-enabled group uses alias
        # instead of name.
        if service == 'neutron':
            extensions = map(lambda x: x['alias'], resp['extensions'])
        elif service == 'swift':
            # Remove Swift general information from extensions list
            resp.pop('swift')
            extensions = resp.keys()
        else:
            extensions = map(lambda x: x['name'], resp['extensions'])

    else:
        extensions = map(lambda x: x['name'], resp)
    if not results.get(service):
        results[service] = {}
    extensions_opt = get_enabled_extensions(service)
    if extensions_opt[0] == 'all':
        results[service]['extensions'] = extensions
        return results
    # Verify that all configured extensions are actually enabled
    for extension in extensions_opt:
        results[service][extension] = extension in extensions
    # Verify that there aren't additional extensions enabled that aren't
    # specified in the config list
    for extension in extensions:
        if extension not in extensions_opt:
            results[service][extension] = False
    return results


def display_results(results, update, replace):
    update_dict = {
        'swift': 'object-storage-feature-enabled',
        'nova': 'compute-feature-enabled',
        'nova_v3': 'compute-feature-enabled',
        'cinder': 'volume-feature-enabled',
        'neutron': 'network-feature-enabled',
    }
    for service in results:
        # If all extensions are specified as being enabled there is no way to
        # verify this so we just assume this to be true
        if results[service].get('extensions'):
            if replace:
                output_list = results[service].get('extensions')
            else:
                output_list = ['all']
        else:
            extension_list = get_enabled_extensions(service)
            output_list = []
            for extension in results[service]:
                if not results[service][extension]:
                    if extension in extension_list:
                        print("%s extension: %s should not be included in the "
                              "list of enabled extensions" % (service,
                                                              extension))
                    else:
                        print("%s extension: %s should be included in the list"
                              " of enabled extensions" % (service, extension))
                        output_list.append(extension)
                else:
                    output_list.append(extension)
        if update:
            # Sort List
            output_list.sort()
            # Convert list to a string
            output_string = ', '.join(output_list)
            if service == 'swift':
                change_option('discoverable_apis', update_dict[service],
                              output_string)
            elif service == 'nova_v3':
                change_option('api_v3_extensions', update_dict[service],
                              output_string)
            else:
                change_option('api_extensions', update_dict[service],
                              output_string)


def check_service_availability(os, update):
    services = []
    avail_services = []
    codename_match = {
        'volume': 'cinder',
        'network': 'neutron',
        'image': 'glance',
        'object_storage': 'swift',
        'compute': 'nova',
        'orchestration': 'heat',
        'metering': 'ceilometer',
        'telemetry': 'ceilometer',
        'data_processing': 'sahara',
        'baremetal': 'ironic',
        'identity': 'keystone',
        'queuing': 'marconi',
        'database': 'trove'
    }
    # Get catalog list for endpoints to use for validation
    __, endpoints = os.endpoints_client.list_endpoints()
    for endpoint in endpoints:
        __, service = os.service_client.get_service(endpoint['service_id'])
        services.append(service['type'])
    # Pull all catalog types from config file and compare against endpoint list
    for cfgname in dir(CONF._config):
        cfg = getattr(CONF, cfgname)
        catalog_type = getattr(cfg, 'catalog_type', None)
        if not catalog_type:
            continue
        else:
            if cfgname == 'identity':
                # Keystone is a required service for tempest
                continue
            if catalog_type not in services:
                if getattr(CONF.service_available, codename_match[cfgname]):
                    print('Endpoint type %s not found either disable service '
                          '%s or fix the catalog_type in the config file' % (
                          catalog_type, codename_match[cfgname]))
                    if update:
                        change_option(codename_match[cfgname],
                                      'service_available', False)
            else:
                if not getattr(CONF.service_available,
                               codename_match[cfgname]):
                    print('Endpoint type %s is available, service %s should be'
                          ' set as available in the config file.' % (
                          catalog_type, codename_match[cfgname]))
                    if update:
                        change_option(codename_match[cfgname],
                                      'service_available', True)
                else:
                    avail_services.append(codename_match[cfgname])
    return avail_services


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--update', action='store_true',
                        help='Update the config file with results from api '
                             'queries. This assumes whatever is set in the '
                             'config file is incorrect. In the case of '
                             'endpoint checks where it could either be the '
                             'incorrect catalog type or the service available '
                             'option the service available option is assumed '
                             'to be incorrect and is thus changed')
    parser.add_argument('-o', '--output',
                        help="Output file to write an updated config file to. "
                             "This has to be a separate file from the "
                             "original config file. If one isn't specified "
                             "with -u the new config file will be printed to "
                             "STDOUT")
    parser.add_argument('-r', '--replace-ext', action='store_true',
                        help="If specified the all option will be replaced "
                             "with a full list of extensions")
    args = parser.parse_args()
    return args


def main():
    print('Running config verification...')
    opts = parse_args()
    update = opts.update
    replace = opts.replace_ext
    global CONF_FILE
    global OUTFILE
    if update:
        CONF_FILE = _get_config_file()
        if opts.output:
            OUTFILE = open(opts.output, 'w+')
    os = clients.ComputeAdminManager(interface='json')
    services = check_service_availability(os, update)
    results = {}
    for service in ['nova', 'nova_v3', 'cinder', 'neutron', 'swift']:
        if service == 'nova_v3' and 'nova' not in services:
            continue
        elif service not in services:
            continue
        results = verify_extensions(os, service, results)
    verify_keystone_api_versions(os, update)
    verify_glance_api_versions(os, update)
    verify_nova_api_versions(os, update)
    verify_cinder_api_versions(os, update)
    display_results(results, update, replace)
    if CONF_FILE:
        CONF_FILE.close()
    OUTFILE.close()


if __name__ == "__main__":
    main()
