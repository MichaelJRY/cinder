# Copyright 2015 IBM Corp.
# Copyright 2012 OpenStack Foundation
# All Rights Reserved.
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
#
"""
Tests for the IBM Storwize family and SVC volume driver.
"""

import ddt
import json
import mock
import paramiko
import random
import re
import time

from oslo_concurrency import processutils
from oslo_utils import importutils
from oslo_utils import units
import six

from cinder import context
from cinder import exception
from cinder.i18n import _
from cinder import objects
from cinder.objects import fields
from cinder import ssh_utils
from cinder import test
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import utils as testutils
from cinder import utils
from cinder.volume import configuration as conf
from cinder.volume.drivers.ibm.storwize_svc import (
    replication as storwize_rep)
from cinder.volume.drivers.ibm.storwize_svc import storwize_const
from cinder.volume.drivers.ibm.storwize_svc import storwize_svc_common
from cinder.volume.drivers.ibm.storwize_svc import storwize_svc_fc
from cinder.volume.drivers.ibm.storwize_svc import storwize_svc_iscsi
from cinder.volume import group_types
from cinder.volume import qos_specs
from cinder.volume import utils as volume_utils
from cinder.volume import volume_types

SVC_POOLS = ['openstack', 'openstack1']


def _get_test_pool(get_all=False):
    if get_all:
        return SVC_POOLS
    else:
        return SVC_POOLS[0]


class StorwizeSVCManagementSimulator(object):
    def __init__(self, pool_name):
        self._flags = {'storwize_svc_volpool_name': pool_name}
        self._volumes_list = {}
        self._hosts_list = {}
        self._mappings_list = {}
        self._fcmappings_list = {}
        self._fcconsistgrp_list = {}
        self._rcrelationship_list = {}
        self._partnership_list = {}
        self._partnershipcandidate_list = {}
        self._system_list = {'storwize-svc-sim': {'id': '0123456789ABCDEF',
                                                  'name': 'storwize-svc-sim'},
                             'aux-svc-sim': {'id': 'ABCDEF0123456789',
                                             'name': 'aux-svc-sim'}}
        self._other_pools = {'openstack2': {}, 'openstack3': {}}
        self._next_cmd_error = {
            'lsportip': '',
            'lsfabric': '',
            'lsiscsiauth': '',
            'lsnodecanister': '',
            'mkvdisk': '',
            'lsvdisk': '',
            'lsfcmap': '',
            'prestartfcmap': '',
            'startfcmap': '',
            'rmfcmap': '',
            'lslicense': '',
            'lsguicapabilities': '',
            'lshost': '',
            'lsrcrelationship': ''
        }
        self._errors = {
            'CMMVC5701E': ('', 'CMMVC5701E No object ID was specified.'),
            'CMMVC6035E': ('', 'CMMVC6035E The action failed as the '
                               'object already exists.'),
            'CMMVC5753E': ('', 'CMMVC5753E The specified object does not '
                               'exist or is not a suitable candidate.'),
            'CMMVC5707E': ('', 'CMMVC5707E Required parameters are missing.'),
            'CMMVC6581E': ('', 'CMMVC6581E The command has failed because '
                               'the maximum number of allowed iSCSI '
                               'qualified names (IQNs) has been reached, '
                               'or the IQN is already assigned or is not '
                               'valid.'),
            'CMMVC5754E': ('', 'CMMVC5754E The specified object does not '
                               'exist, or the name supplied does not meet '
                               'the naming rules.'),
            'CMMVC6071E': ('', 'CMMVC6071E The VDisk-to-host mapping was '
                               'not created because the VDisk is already '
                               'mapped to a host.'),
            'CMMVC5879E': ('', 'CMMVC5879E The VDisk-to-host mapping was '
                               'not created because a VDisk is already '
                               'mapped to this host with this SCSI LUN.'),
            'CMMVC5840E': ('', 'CMMVC5840E The virtual disk (VDisk) was '
                               'not deleted because it is mapped to a '
                               'host or because it is part of a FlashCopy '
                               'or Remote Copy mapping, or is involved in '
                               'an image mode migrate.'),
            'CMMVC6527E': ('', 'CMMVC6527E The name that you have entered '
                               'is not valid. The name can contain letters, '
                               'numbers, spaces, periods, dashes, and '
                               'underscores. The name must begin with a '
                               'letter or an underscore. The name must not '
                               'begin or end with a space.'),
            'CMMVC5871E': ('', 'CMMVC5871E The action failed because one or '
                               'more of the configured port names is in a '
                               'mapping.'),
            'CMMVC5924E': ('', 'CMMVC5924E The FlashCopy mapping was not '
                               'created because the source and target '
                               'virtual disks (VDisks) are different sizes.'),
            'CMMVC6303E': ('', 'CMMVC6303E The create failed because the '
                               'source and target VDisks are the same.'),
            'CMMVC7050E': ('', 'CMMVC7050E The command failed because at '
                               'least one node in the I/O group does not '
                               'support compressed VDisks.'),
            'CMMVC6430E': ('', 'CMMVC6430E The command failed because the '
                               'target and source managed disk groups must '
                               'be different.'),
            'CMMVC6353E': ('', 'CMMVC6353E The command failed because the '
                               'copy specified does not exist.'),
            'CMMVC6446E': ('', 'The command failed because the managed disk '
                               'groups have different extent sizes.'),
            # Catch-all for invalid state transitions:
            'CMMVC5903E': ('', 'CMMVC5903E The FlashCopy mapping was not '
                               'changed because the mapping or consistency '
                               'group is another state.'),
            'CMMVC5709E': ('', 'CMMVC5709E [-%(VALUE)s] is not a supported '
                               'parameter.'),
            'CMMVC5982E': ('', 'CMMVC5982E The operation was not performed '
                               'because it is not valid given the current '
                               'relationship state.'),
            'CMMVC5963E': ('', 'CMMVC5963E No direction has been defined.'),

        }
        self._fc_transitions = {'begin': {'make': 'idle_or_copied'},
                                'idle_or_copied': {'prepare': 'preparing',
                                                   'delete': 'end',
                                                   'delete_force': 'end'},
                                'preparing': {'flush_failed': 'stopped',
                                              'wait': 'prepared'},
                                'end': None,
                                'stopped': {'prepare': 'preparing',
                                            'delete_force': 'end'},
                                'prepared': {'stop': 'stopped',
                                             'start': 'copying'},
                                'copying': {'wait': 'idle_or_copied',
                                            'stop': 'stopping'},
                                # Assume the worst case where stopping->stopped
                                # rather than stopping idle_or_copied
                                'stopping': {'wait': 'stopped'},
                                }

        self._fc_cg_transitions = {'begin': {'make': 'empty'},
                                   'empty': {'add': 'idle_or_copied'},
                                   'idle_or_copied': {'prepare': 'preparing',
                                                      'delete': 'end',
                                                      'delete_force': 'end'},
                                   'preparing': {'flush_failed': 'stopped',
                                                 'wait': 'prepared'},
                                   'end': None,
                                   'stopped': {'prepare': 'preparing',
                                               'delete_force': 'end'},
                                   'prepared': {'stop': 'stopped',
                                                'start': 'copying',
                                                'delete_force': 'end',
                                                'delete': 'end'},
                                   'copying': {'wait': 'idle_or_copied',
                                               'stop': 'stopping',
                                               'delete_force': 'end',
                                               'delete': 'end'},
                                   # Assume the case where stopping->stopped
                                   # rather than stopping idle_or_copied
                                   'stopping': {'wait': 'stopped'},
                                   }
        self._rc_transitions = {'inconsistent_stopped':
                                {'start': 'inconsistent_copying',
                                 'stop': 'inconsistent_stopped',
                                 'delete': 'end',
                                 'delete_force': 'end'},
                                'inconsistent_copying': {
                                    'wait': 'consistent_synchronized',
                                    'start': 'inconsistent_copying',
                                    'stop': 'inconsistent_stopped',
                                    'delete': 'end',
                                    'delete_force': 'end'},
                                'consistent_synchronized': {
                                    'start': 'consistent_synchronized',
                                    'stop': 'consistent_stopped',
                                    'stop_access': 'idling',
                                    'delete': 'end',
                                    'delete_force': 'end'},
                                'consistent_stopped':
                                {'start': 'consistent_synchronized',
                                 'stop': 'consistent_stopped',
                                 'delete': 'end',
                                 'delete_force': 'end'},
                                'end': None,
                                'idling': {
                                    'start': 'inconsistent_copying',
                                    'stop': 'inconsistent_stopped',
                                    'stop_access': 'idling',
                                    'delete': 'end',
                                    'delete_force': 'end'},
                                }

    def _state_transition(self, function, fcmap):
        if (function == 'wait' and
                'wait' not in self._fc_transitions[fcmap['status']]):
            return ('', '')

        if fcmap['status'] == 'copying' and function == 'wait':
            if fcmap['copyrate'] != '0':
                if fcmap['progress'] == '0':
                    fcmap['progress'] = '50'
                else:
                    fcmap['progress'] = '100'
                    fcmap['status'] = 'idle_or_copied'
            return ('', '')
        else:
            try:
                curr_state = fcmap['status']
                fcmap['status'] = self._fc_transitions[curr_state][function]
                return ('', '')
            except Exception:
                return self._errors['CMMVC5903E']

    def _fc_cg_state_transition(self, function, fc_consistgrp):
        if (function == 'wait' and
                'wait' not in self._fc_transitions[fc_consistgrp['status']]):
            return ('', '')

        try:
            curr_state = fc_consistgrp['status']
            fc_consistgrp['status'] \
                = self._fc_cg_transitions[curr_state][function]
            return ('', '')
        except Exception:
            return self._errors['CMMVC5903E']

    # Find an unused ID
    @staticmethod
    def _find_unused_id(d):
        ids = []
        for v in d.values():
            ids.append(int(v['id']))
        ids.sort()
        for index, n in enumerate(ids):
            if n > index:
                return six.text_type(index)
        return six.text_type(len(ids))

    # Check if name is valid
    @staticmethod
    def _is_invalid_name(name):
        if re.match(r'^[a-zA-Z_][\w._-]*$', name):
            return False
        return True

    # Convert argument string to dictionary
    @staticmethod
    def _cmd_to_dict(arg_list):
        no_param_args = [
            'autodelete',
            'bytes',
            'compressed',
            'force',
            'nohdr',
            'nofmtdisk',
            'global',
            'access',
            'start'
        ]
        one_param_args = [
            'chapsecret',
            'cleanrate',
            'copy',
            'copyrate',
            'delim',
            'easytier',
            'filtervalue',
            'grainsize',
            'hbawwpn',
            'host',
            'iogrp',
            'iscsiname',
            'mdiskgrp',
            'name',
            'rsize',
            'scsi',
            'size',
            'source',
            'target',
            'unit',
            'vdisk',
            'warning',
            'wwpn',
            'primary',
            'consistgrp',
            'master',
            'aux',
            'cluster',
            'linkbandwidthmbits',
            'backgroundcopyrate',
            'copies'
        ]
        no_or_one_param_args = [
            'autoexpand',
        ]

        # Handle the special case of lsnode which is a two-word command
        # Use the one word version of the command internally
        if arg_list[0] in ('svcinfo', 'svctask'):
            if arg_list[1] == 'lsnode':
                if len(arg_list) > 4:  # e.g. svcinfo lsnode -delim ! <node id>
                    ret = {'cmd': 'lsnode', 'node_id': arg_list[-1]}
                else:
                    ret = {'cmd': 'lsnodecanister'}
            else:
                ret = {'cmd': arg_list[1]}
            arg_list.pop(0)
        else:
            ret = {'cmd': arg_list[0]}

        skip = False
        for i in range(1, len(arg_list)):
            if skip:
                skip = False
                continue
            # Check for a quoted command argument for volumes and strip
            # quotes so that the simulater can match it later. Just
            # match against test naming convensions for now.
            if arg_list[i][0] == '"' and ('volume' in arg_list[i] or
                                          'snapshot' in arg_list[i]):
                arg_list[i] = arg_list[i][1:-1]
            if arg_list[i][0] == '-':
                if arg_list[i][1:] in no_param_args:
                    ret[arg_list[i][1:]] = True
                elif arg_list[i][1:] in one_param_args:
                    ret[arg_list[i][1:]] = arg_list[i + 1]
                    skip = True
                elif arg_list[i][1:] in no_or_one_param_args:
                    if i == (len(arg_list) - 1) or arg_list[i + 1][0] == '-':
                        ret[arg_list[i][1:]] = True
                    else:
                        ret[arg_list[i][1:]] = arg_list[i + 1]
                        skip = True
                else:
                    raise exception.InvalidInput(
                        reason=_('unrecognized argument %s') % arg_list[i])
            else:
                ret['obj'] = arg_list[i]
        return ret

    @staticmethod
    def _print_info_cmd(rows, delim=' ', nohdr=False, **kwargs):
        """Generic function for printing information."""
        if nohdr:
            del rows[0]

        for index in range(len(rows)):
            rows[index] = delim.join(rows[index])
        return ('%s' % '\n'.join(rows), '')

    @staticmethod
    def _print_info_obj_cmd(header, row, delim=' ', nohdr=False):
        """Generic function for printing information for a specific object."""
        objrows = []
        for idx, val in enumerate(header):
            objrows.append([val, row[idx]])

        if nohdr:
            for index in range(len(objrows)):
                objrows[index] = ' '.join(objrows[index][1:])
        for index in range(len(objrows)):
            objrows[index] = delim.join(objrows[index])
        return ('%s' % '\n'.join(objrows), '')

    @staticmethod
    def _convert_bytes_units(bytestr):
        num = int(bytestr)
        unit_array = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        unit_index = 0

        while num > 1024:
            num = num / 1024
            unit_index += 1

        return '%d%s' % (num, unit_array[unit_index])

    @staticmethod
    def _convert_units_bytes(num, unit):
        unit_array = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
        unit_index = 0

        while unit.lower() != unit_array[unit_index].lower():
            num = num * 1024
            unit_index += 1

        return six.text_type(num)

    def _cmd_lslicense(self, **kwargs):
        rows = [None] * 3
        rows[0] = ['used_compression_capacity', '0.08']
        rows[1] = ['license_compression_capacity', '0']
        if self._next_cmd_error['lslicense'] == 'no_compression':
            self._next_cmd_error['lslicense'] = ''
            rows[2] = ['license_compression_enclosures', '0']
        else:
            rows[2] = ['license_compression_enclosures', '1']
        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_lsguicapabilities(self, **kwargs):
        rows = [None] * 2
        if self._next_cmd_error['lsguicapabilities'] == 'no_compression':
            self._next_cmd_error['lsguicapabilities'] = ''
            rows[0] = ['license_scheme', '0']
        else:
            rows[0] = ['license_scheme', 'flex']
        rows[1] = ['product_key', storwize_const.DEV_MODEL_SVC]
        return self._print_info_cmd(rows=rows, **kwargs)

    # Print mostly made-up stuff in the correct syntax
    def _cmd_lssystem(self, **kwargs):
        rows = [None] * 3
        rows[0] = ['id', '0123456789ABCDEF']
        rows[1] = ['name', 'storwize-svc-sim']
        rows[2] = ['code_level', '7.2.0.0 (build 87.0.1311291000)']
        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_lssystem_aux(self, **kwargs):
        rows = [None] * 3
        rows[0] = ['id', 'ABCDEF0123456789']
        rows[1] = ['name', 'aux-svc-sim']
        rows[2] = ['code_level', '7.2.0.0 (build 87.0.1311291000)']
        return self._print_info_cmd(rows=rows, **kwargs)

    # Print mostly made-up stuff in the correct syntax, assume -bytes passed
    def _cmd_lsmdiskgrp(self, **kwargs):
        pool_num = len(self._flags['storwize_svc_volpool_name'])
        rows = []
        rows.append(['id', 'name', 'status', 'mdisk_count',
                     'vdisk_count', 'capacity', 'extent_size',
                     'free_capacity', 'virtual_capacity', 'used_capacity',
                     'real_capacity', 'overallocation', 'warning',
                     'easy_tier', 'easy_tier_status'])
        for i in range(pool_num):
            row_data = [str(i + 1),
                        self._flags['storwize_svc_volpool_name'][i], 'online',
                        '1', six.text_type(len(self._volumes_list)),
                        '3573412790272', '256', '3529926246400',
                        '1693247906775',
                        '26843545600', '38203734097', '47', '80', 'auto',
                        'inactive']
            rows.append(row_data)
        rows.append([str(pool_num + 1), 'openstack2', 'online',
                     '1', '0', '3573412790272', '256',
                     '3529432325160', '1693247906775', '26843545600',
                     '38203734097', '47', '80', 'auto', 'inactive'])
        rows.append([str(pool_num + 2), 'openstack3', 'online',
                     '1', '0', '3573412790272', '128',
                     '3529432325160', '1693247906775', '26843545600',
                     '38203734097', '47', '80', 'auto', 'inactive'])
        if 'obj' not in kwargs:
            return self._print_info_cmd(rows=rows, **kwargs)
        else:
            pool_name = kwargs['obj'].strip('\'\"')
            if pool_name == kwargs['obj']:
                raise exception.InvalidInput(
                    reason=_('obj missing quotes %s') % kwargs['obj'])
            elif pool_name in self._flags['storwize_svc_volpool_name']:
                for each_row in rows:
                    if pool_name in each_row:
                        row = each_row
                        break
            elif pool_name == 'openstack2':
                row = rows[-2]
            elif pool_name == 'openstack3':
                row = rows[-1]
            else:
                return self._errors['CMMVC5754E']
            objrows = []
            for idx, val in enumerate(rows[0]):
                objrows.append([val, row[idx]])
            if 'nohdr' in kwargs:
                for index in range(len(objrows)):
                    objrows[index] = ' '.join(objrows[index][1:])

            if 'delim' in kwargs:
                for index in range(len(objrows)):
                    objrows[index] = kwargs['delim'].join(objrows[index])

            return ('%s' % '\n'.join(objrows), '')

    def _get_mdiskgrp_id(self, mdiskgrp):
        grp_num = len(self._flags['storwize_svc_volpool_name'])
        if mdiskgrp in self._flags['storwize_svc_volpool_name']:
            for i in range(grp_num):
                if mdiskgrp == self._flags['storwize_svc_volpool_name'][i]:
                    return i + 1
        elif mdiskgrp == 'openstack2':
            return grp_num + 1
        elif mdiskgrp == 'openstack3':
            return grp_num + 2
        else:
            return None

    # Print mostly made-up stuff in the correct syntax
    def _cmd_lsnodecanister(self, **kwargs):
        rows = [None] * 3
        rows[0] = ['id', 'name', 'UPS_serial_number', 'WWNN', 'status',
                   'IO_group_id', 'IO_group_name', 'config_node',
                   'UPS_unique_id', 'hardware', 'iscsi_name', 'iscsi_alias',
                   'panel_name', 'enclosure_id', 'canister_id',
                   'enclosure_serial_number']
        rows[1] = ['1', 'node1', '', '123456789ABCDEF0', 'online', '0',
                   'io_grp0',
                   'yes', '123456789ABCDEF0', '100',
                   'iqn.1982-01.com.ibm:1234.sim.node1', '', '01-1', '1', '1',
                   '0123ABC']
        rows[2] = ['2', 'node2', '', '123456789ABCDEF1', 'online', '0',
                   'io_grp0',
                   'no', '123456789ABCDEF1', '100',
                   'iqn.1982-01.com.ibm:1234.sim.node2', '', '01-2', '1', '2',
                   '0123ABC']

        if self._next_cmd_error['lsnodecanister'] == 'header_mismatch':
            rows[0].pop(2)
            self._next_cmd_error['lsnodecanister'] = ''
        if self._next_cmd_error['lsnodecanister'] == 'remove_field':
            for row in rows:
                row.pop(0)
            self._next_cmd_error['lsnodecanister'] = ''

        return self._print_info_cmd(rows=rows, **kwargs)

    # Print information of every single node of SVC
    def _cmd_lsnode(self, **kwargs):
        node_infos = dict()
        node_infos['1'] = r'''id!1
name!node1
port_id!500507680210C744
port_status!active
port_speed!8Gb
port_id!500507680220C744
port_status!active
port_speed!8Gb
'''
        node_infos['2'] = r'''id!2
name!node2
port_id!500507680220C745
port_status!active
port_speed!8Gb
port_id!500507680230C745
port_status!inactive
port_speed!N/A
'''
        node_id = kwargs.get('node_id', None)
        stdout = node_infos.get(node_id, '')
        return stdout, ''

    # Print made up stuff for the ports
    def _cmd_lsportfc(self, **kwargs):
        node_1 = [None] * 7
        node_1[0] = ['id', 'fc_io_port_id', 'port_id', 'type',
                     'port_speed', 'node_id', 'node_name', 'WWPN',
                     'nportid', 'status', 'attachment']
        node_1[1] = ['0', '1', '1', 'fc', '8Gb', '1', 'node1',
                     '5005076802132ADE', '012E00', 'active', 'switch']
        node_1[2] = ['1', '2', '2', 'fc', '8Gb', '1', 'node1',
                     '5005076802232ADE', '012E00', 'active', 'switch']
        node_1[3] = ['2', '3', '3', 'fc', '8Gb', '1', 'node1',
                     '5005076802332ADE', '9B0600', 'active', 'switch']
        node_1[4] = ['3', '4', '4', 'fc', '8Gb', '1', 'node1',
                     '5005076802432ADE', '012A00', 'active', 'switch']
        node_1[5] = ['4', '5', '5', 'fc', '8Gb', '1', 'node1',
                     '5005076802532ADE', '014A00', 'active', 'switch']
        node_1[6] = ['5', '6', '4', 'ethernet', 'N/A', '1', 'node1',
                     '5005076802632ADE', '000000',
                     'inactive_unconfigured', 'none']

        node_2 = [None] * 7
        node_2[0] = ['id', 'fc_io_port_id', 'port_id', 'type',
                     'port_speed', 'node_id', 'node_name', 'WWPN',
                     'nportid', 'status', 'attachment']
        node_2[1] = ['6', '7', '7', 'fc', '8Gb', '2', 'node2',
                     '5005086802132ADE', '012E00', 'active', 'switch']
        node_2[2] = ['7', '8', '8', 'fc', '8Gb', '2', 'node2',
                     '5005086802232ADE', '012E00', 'active', 'switch']
        node_2[3] = ['8', '9', '9', 'fc', '8Gb', '2', 'node2',
                     '5005086802332ADE', '9B0600', 'active', 'switch']
        node_2[4] = ['9', '10', '10', 'fc', '8Gb', '2', 'node2',
                     '5005086802432ADE', '012A00', 'active', 'switch']
        node_2[5] = ['10', '11', '11', 'fc', '8Gb', '2', 'node2',
                     '5005086802532ADE', '014A00', 'active', 'switch']
        node_2[6] = ['11', '12', '12', 'ethernet', 'N/A', '2', 'node2',
                     '5005086802632ADE', '000000',
                     'inactive_unconfigured', 'none']
        node_infos = [node_1, node_2]
        node_id = int(kwargs['filtervalue'].split('=')[1]) - 1

        return self._print_info_cmd(rows=node_infos[node_id], **kwargs)

    # Print mostly made-up stuff in the correct syntax
    def _cmd_lsportip(self, **kwargs):
        if self._next_cmd_error['lsportip'] == 'ip_no_config':
            self._next_cmd_error['lsportip'] = ''
            ip_addr1 = ''
            ip_addr2 = ''
            gw = ''
        else:
            ip_addr1 = '1.234.56.78'
            ip_addr2 = '1.234.56.79'
            ip_addr3 = '1.234.56.80'
            ip_addr4 = '1.234.56.81'
            gw = '1.234.56.1'

        rows = [None] * 17
        rows[0] = ['id', 'node_id', 'node_name', 'IP_address', 'mask',
                   'gateway', 'IP_address_6', 'prefix_6', 'gateway_6', 'MAC',
                   'duplex', 'state', 'speed', 'failover', 'link_state']
        rows[1] = ['1', '1', 'node1', ip_addr1, '255.255.255.0',
                   gw, '', '', '', '01:23:45:67:89:00', 'Full',
                   'online', '1Gb/s', 'no', 'active']
        rows[2] = ['1', '1', 'node1', '', '', '', '', '', '',
                   '01:23:45:67:89:00', 'Full', 'online', '1Gb/s', 'yes', '']
        rows[3] = ['2', '1', 'node1', ip_addr3, '255.255.255.0',
                   gw, '', '', '', '01:23:45:67:89:01', 'Full',
                   'configured', '1Gb/s', 'no', 'active']
        rows[4] = ['2', '1', 'node1', '', '', '', '', '', '',
                   '01:23:45:67:89:01', 'Full', 'unconfigured', '1Gb/s',
                   'yes', 'inactive']
        rows[5] = ['3', '1', 'node1', '', '', '', '', '', '', '', '',
                   'unconfigured', '', 'no', '']
        rows[6] = ['3', '1', 'node1', '', '', '', '', '', '', '', '',
                   'unconfigured', '', 'yes', '']
        rows[7] = ['4', '1', 'node1', '', '', '', '', '', '', '', '',
                   'unconfigured', '', 'no', '']
        rows[8] = ['4', '1', 'node1', '', '', '', '', '', '', '', '',
                   'unconfigured', '', 'yes', '']
        rows[9] = ['1', '2', 'node2', ip_addr2, '255.255.255.0',
                   gw, '', '', '', '01:23:45:67:89:02', 'Full',
                   'online', '1Gb/s', 'no', '']
        rows[10] = ['1', '2', 'node2', '', '', '', '', '', '',
                    '01:23:45:67:89:02', 'Full', 'online', '1Gb/s', 'yes', '']
        rows[11] = ['2', '2', 'node2', ip_addr4, '255.255.255.0',
                    gw, '', '', '', '01:23:45:67:89:03', 'Full',
                    'configured', '1Gb/s', 'no', 'inactive']
        rows[12] = ['2', '2', 'node2', '', '', '', '', '', '',
                    '01:23:45:67:89:03', 'Full', 'unconfigured', '1Gb/s',
                    'yes', '']
        rows[13] = ['3', '2', 'node2', '', '', '', '', '', '', '', '',
                    'unconfigured', '', 'no', '']
        rows[14] = ['3', '2', 'node2', '', '', '', '', '', '', '', '',
                    'unconfigured', '', 'yes', '']
        rows[15] = ['4', '2', 'node2', '', '', '', '', '', '', '', '',
                    'unconfigured', '', 'no', '']
        rows[16] = ['4', '2', 'node2', '', '', '', '', '', '', '', '',
                    'unconfigured', '', 'yes', '']

        if self._next_cmd_error['lsportip'] == 'header_mismatch':
            rows[0].pop(2)
            self._next_cmd_error['lsportip'] = ''
        if self._next_cmd_error['lsportip'] == 'remove_field':
            for row in rows:
                row.pop(1)
            self._next_cmd_error['lsportip'] = ''

        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_lsfabric(self, **kwargs):
        if self._next_cmd_error['lsfabric'] == 'no_hosts':
            return ('', '')
        host_name = kwargs['host'].strip('\'\"') if 'host' in kwargs else None
        target_wwpn = kwargs['wwpn'] if 'wwpn' in kwargs else None
        host_infos = []
        for hv in self._hosts_list.values():
            if (not host_name) or (hv['host_name'] == host_name):
                if not target_wwpn or target_wwpn in hv['wwpns']:
                    host_infos.append(hv)
                    break
        if not len(host_infos):
            return ('', '')
        rows = []
        rows.append(['remote_wwpn', 'remote_nportid', 'id', 'node_name',
                     'local_wwpn', 'local_port', 'local_nportid', 'state',
                     'name', 'cluster_name', 'type'])
        for host_info in host_infos:
            for wwpn in host_info['wwpns']:
                rows.append([wwpn, '123456', host_info['id'], 'nodeN',
                             'AABBCCDDEEFF0011', '1', '0123ABC', 'active',
                             host_info['host_name'], '', 'host'])
        if self._next_cmd_error['lsfabric'] == 'header_mismatch':
            rows[0].pop(0)
            self._next_cmd_error['lsfabric'] = ''
        if self._next_cmd_error['lsfabric'] == 'remove_field':
            for row in rows:
                row.pop(0)
            self._next_cmd_error['lsfabric'] = ''
        if self._next_cmd_error['lsfabric'] == 'remove_rows':
            rows = []
        return self._print_info_cmd(rows=rows, **kwargs)

    # Create a vdisk
    def _cmd_mkvdisk(self, **kwargs):
        # We only save the id/uid, name, and size - all else will be made up
        volume_info = {}
        volume_info['id'] = self._find_unused_id(self._volumes_list)
        volume_info['uid'] = ('ABCDEF' * 3) + ('0' * 14) + volume_info['id']

        mdiskgrp = kwargs['mdiskgrp'].strip('\'\"')
        sec_pool = None
        is_mirror_vol = False
        if 'copies' in kwargs:
            # it is a mirror volume
            pool_split = mdiskgrp.split(':')
            if len(pool_split) != 2:
                raise exception.InvalidInput(
                    reason=_('mdiskgrp %s is invalid for mirror '
                             'volume') % kwargs['mdiskgrp'])
            else:
                is_mirror_vol = True
                mdiskgrp = pool_split[0]
                sec_pool = pool_split[1]

        if mdiskgrp == kwargs['mdiskgrp']:
            raise exception.InvalidInput(
                reason=_('mdiskgrp missing quotes %s') % kwargs['mdiskgrp'])
        mdiskgrp_id = self._get_mdiskgrp_id(mdiskgrp)
        sec_pool_id = self._get_mdiskgrp_id(sec_pool)
        volume_info['mdisk_grp_name'] = mdiskgrp
        volume_info['mdisk_grp_id'] = str(mdiskgrp_id)

        if 'name' in kwargs:
            volume_info['name'] = kwargs['name'].strip('\'\"')
        else:
            volume_info['name'] = 'vdisk' + volume_info['id']

        # Assume size and unit are given, store it in bytes
        capacity = int(kwargs['size'])
        unit = kwargs['unit']
        volume_info['capacity'] = self._convert_units_bytes(capacity, unit)
        volume_info['IO_group_id'] = kwargs['iogrp']
        volume_info['IO_group_name'] = 'io_grp%s' % kwargs['iogrp']
        volume_info['RC_name'] = ''
        volume_info['RC_id'] = ''

        if 'easytier' in kwargs:
            if kwargs['easytier'] == 'on':
                volume_info['easy_tier'] = 'on'
            else:
                volume_info['easy_tier'] = 'off'

        if 'rsize' in kwargs:
            volume_info['formatted'] = 'no'
            # Fake numbers
            volume_info['used_capacity'] = '786432'
            volume_info['real_capacity'] = '21474816'
            volume_info['free_capacity'] = '38219264'
            if 'warning' in kwargs:
                volume_info['warning'] = kwargs['warning'].rstrip('%')
            else:
                volume_info['warning'] = '80'
            if 'autoexpand' in kwargs:
                volume_info['autoexpand'] = 'on'
            else:
                volume_info['autoexpand'] = 'off'
            if 'grainsize' in kwargs:
                volume_info['grainsize'] = kwargs['grainsize']
            else:
                volume_info['grainsize'] = '32'
            if 'compressed' in kwargs:
                volume_info['compressed_copy'] = 'yes'
            else:
                volume_info['compressed_copy'] = 'no'
        else:
            volume_info['used_capacity'] = volume_info['capacity']
            volume_info['real_capacity'] = volume_info['capacity']
            volume_info['free_capacity'] = '0'
            volume_info['warning'] = ''
            volume_info['autoexpand'] = ''
            volume_info['grainsize'] = ''
            volume_info['compressed_copy'] = 'no'
            volume_info['formatted'] = 'yes'
            if 'nofmtdisk' in kwargs:
                if kwargs['nofmtdisk']:
                    volume_info['formatted'] = 'no'

        vol_cp = {'id': '0',
                  'status': 'online',
                  'sync': 'yes',
                  'primary': 'yes',
                  'mdisk_grp_id': str(mdiskgrp_id),
                  'mdisk_grp_name': mdiskgrp,
                  'easy_tier': volume_info['easy_tier'],
                  'compressed_copy': volume_info['compressed_copy']}
        volume_info['copies'] = {'0': vol_cp}
        if is_mirror_vol:
            vol_cp1 = {'id': '1',
                       'status': 'online',
                       'sync': 'yes',
                       'primary': 'no',
                       'mdisk_grp_id': str(sec_pool_id),
                       'mdisk_grp_name': sec_pool,
                       'easy_tier': volume_info['easy_tier'],
                       'compressed_copy': volume_info['compressed_copy']}
            volume_info['copies']['1'] = vol_cp1

        if volume_info['name'] in self._volumes_list:
            return self._errors['CMMVC6035E']
        else:
            self._volumes_list[volume_info['name']] = volume_info
            return ('Virtual Disk, id [%s], successfully created' %
                    (volume_info['id']), '')

    # Delete a vdisk
    def _cmd_rmvdisk(self, **kwargs):
        force = True if 'force' in kwargs else False

        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')

        if vol_name not in self._volumes_list:
            return self._errors['CMMVC5753E']

        if not force:
            for mapping in self._mappings_list.values():
                if mapping['vol'] == vol_name:
                    return self._errors['CMMVC5840E']
            for fcmap in self._fcmappings_list.values():
                if ((fcmap['source'] == vol_name) or
                        (fcmap['target'] == vol_name)):
                    return self._errors['CMMVC5840E']

        del self._volumes_list[vol_name]
        return ('', '')

    def _cmd_expandvdisksize(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')

        # Assume unit is gb
        if 'size' not in kwargs:
            return self._errors['CMMVC5707E']
        size = int(kwargs['size'])

        if vol_name not in self._volumes_list:
            return self._errors['CMMVC5753E']

        curr_size = int(self._volumes_list[vol_name]['capacity'])
        addition = size * units.Gi
        self._volumes_list[vol_name]['capacity'] = (
            six.text_type(curr_size + addition))
        return ('', '')

    def _get_fcmap_info(self, vol_name):
        ret_vals = {
            'fc_id': '',
            'fc_name': '',
            'fc_map_count': '0',
        }
        for fcmap in self._fcmappings_list.values():
            if ((fcmap['source'] == vol_name) or
                    (fcmap['target'] == vol_name)):
                ret_vals['fc_id'] = fcmap['id']
                ret_vals['fc_name'] = fcmap['name']
                ret_vals['fc_map_count'] = '1'
        return ret_vals

    # List information about vdisks
    def _cmd_lsvdisk(self, **kwargs):
        rows = []
        rows.append(['id', 'name', 'IO_group_id', 'IO_group_name',
                     'status', 'mdisk_grp_id', 'mdisk_grp_name',
                     'capacity', 'type', 'FC_id', 'FC_name', 'RC_id',
                     'RC_name', 'vdisk_UID', 'fc_map_count', 'copy_count',
                     'fast_write_state', 'se_copy_count', 'RC_change'])

        for vol in self._volumes_list.values():
            if (('filtervalue' not in kwargs) or
               (kwargs['filtervalue'] == 'name=' + vol['name']) or
               (kwargs['filtervalue'] == 'vdisk_UID=' + vol['uid'])):
                fcmap_info = self._get_fcmap_info(vol['name'])

                if 'bytes' in kwargs:
                    cap = self._convert_bytes_units(vol['capacity'])
                else:
                    cap = vol['capacity']
                rows.append([six.text_type(vol['id']), vol['name'],
                             vol['IO_group_id'],
                             vol['IO_group_name'], 'online', '0',
                             _get_test_pool(),
                             cap, 'striped',
                             fcmap_info['fc_id'], fcmap_info['fc_name'],
                             '', '', vol['uid'],
                             fcmap_info['fc_map_count'], '1', 'empty',
                             '1', 'no'])
        if 'obj' not in kwargs:
            return self._print_info_cmd(rows=rows, **kwargs)
        else:
            if kwargs['obj'] not in self._volumes_list:
                return self._errors['CMMVC5754E']
            vol = self._volumes_list[kwargs['obj']]
            fcmap_info = self._get_fcmap_info(vol['name'])
            cap = vol['capacity']
            cap_u = vol['used_capacity']
            cap_r = vol['real_capacity']
            cap_f = vol['free_capacity']
            if 'bytes' not in kwargs:
                for item in [cap, cap_u, cap_r, cap_f]:
                    item = self._convert_bytes_units(item)
            rows = []

            rows.append(['id', six.text_type(vol['id'])])
            rows.append(['name', vol['name']])
            rows.append(['IO_group_id', vol['IO_group_id']])
            rows.append(['IO_group_name', vol['IO_group_name']])
            rows.append(['status', 'online'])
            rows.append(['capacity', cap])
            rows.append(['formatted', vol['formatted']])
            rows.append(['mdisk_id', ''])
            rows.append(['mdisk_name', ''])
            rows.append(['FC_id', fcmap_info['fc_id']])
            rows.append(['FC_name', fcmap_info['fc_name']])
            rows.append(['RC_id', vol['RC_id']])
            rows.append(['RC_name', vol['RC_name']])
            rows.append(['vdisk_UID', vol['uid']])
            rows.append(['throttling', '0'])

            if self._next_cmd_error['lsvdisk'] == 'blank_pref_node':
                rows.append(['preferred_node_id', ''])
                self._next_cmd_error['lsvdisk'] = ''
            elif self._next_cmd_error['lsvdisk'] == 'no_pref_node':
                self._next_cmd_error['lsvdisk'] = ''
            else:
                rows.append(['preferred_node_id', '1'])
            rows.append(['fast_write_state', 'empty'])
            rows.append(['cache', 'readwrite'])
            rows.append(['udid', ''])
            rows.append(['fc_map_count', fcmap_info['fc_map_count']])
            rows.append(['sync_rate', '50'])
            rows.append(['copy_count', '1'])
            rows.append(['se_copy_count', '0'])
            rows.append(['mirror_write_priority', 'latency'])
            rows.append(['RC_change', 'no'])

            for copy in vol['copies'].values():
                rows.append(['copy_id', copy['id']])
                rows.append(['status', copy['status']])
                rows.append(['primary', copy['primary']])
                rows.append(['mdisk_grp_id', copy['mdisk_grp_id']])
                rows.append(['mdisk_grp_name', copy['mdisk_grp_name']])
                rows.append(['type', 'striped'])
                rows.append(['used_capacity', cap_u])
                rows.append(['real_capacity', cap_r])
                rows.append(['free_capacity', cap_f])
                rows.append(['easy_tier', copy['easy_tier']])
                rows.append(['compressed_copy', copy['compressed_copy']])
                rows.append(['autoexpand', vol['autoexpand']])
                rows.append(['warning', vol['warning']])
                rows.append(['grainsize', vol['grainsize']])

            if 'nohdr' in kwargs:
                for index in range(len(rows)):
                    rows[index] = ' '.join(rows[index][1:])

            if 'delim' in kwargs:
                for index in range(len(rows)):
                    rows[index] = kwargs['delim'].join(rows[index])
            return ('%s' % '\n'.join(rows), '')

    def _cmd_lsiogrp(self, **kwargs):
        rows = [None] * 6
        rows[0] = ['id', 'name', 'node_count', 'vdisk_count', 'host_count']
        rows[1] = ['0', 'io_grp0', '2', '0', '4']
        rows[2] = ['1', 'io_grp1', '2', '0', '4']
        rows[3] = ['2', 'io_grp2', '0', '0', '4']
        rows[4] = ['3', 'io_grp3', '0', '0', '4']
        rows[5] = ['4', 'recovery_io_grp', '0', '0', '0']
        return self._print_info_cmd(rows=rows, **kwargs)

    def _add_port_to_host(self, host_info, **kwargs):
        if 'iscsiname' in kwargs:
            added_key = 'iscsi_names'
            added_val = kwargs['iscsiname'].strip('\'\"')
        elif 'hbawwpn' in kwargs:
            added_key = 'wwpns'
            added_val = kwargs['hbawwpn'].strip('\'\"')
        else:
            return self._errors['CMMVC5707E']

        host_info[added_key].append(added_val)

        for v in self._hosts_list.values():
            if v['id'] == host_info['id']:
                continue
            for port in v[added_key]:
                if port == added_val:
                    return self._errors['CMMVC6581E']
        return ('', '')

    # Make a host
    def _cmd_mkhost(self, **kwargs):
        host_info = {}
        host_info['id'] = self._find_unused_id(self._hosts_list)

        if 'name' in kwargs:
            host_name = kwargs['name'].strip('\'\"')
        else:
            host_name = 'host' + six.text_type(host_info['id'])

        if self._is_invalid_name(host_name):
            return self._errors['CMMVC6527E']

        if host_name in self._hosts_list:
            return self._errors['CMMVC6035E']

        host_info['host_name'] = host_name
        host_info['iscsi_names'] = []
        host_info['wwpns'] = []

        out, err = self._add_port_to_host(host_info, **kwargs)
        if not len(err):
            self._hosts_list[host_name] = host_info
            return ('Host, id [%s], successfully created' %
                    (host_info['id']), '')
        else:
            return (out, err)

    # Add ports to an existing host
    def _cmd_addhostport(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        host_name = kwargs['obj'].strip('\'\"')

        if host_name not in self._hosts_list:
            return self._errors['CMMVC5753E']

        host_info = self._hosts_list[host_name]
        return self._add_port_to_host(host_info, **kwargs)

    # Change host properties
    def _cmd_chhost(self, **kwargs):
        if 'chapsecret' not in kwargs:
            return self._errors['CMMVC5707E']
        secret = kwargs['obj'].strip('\'\"')

        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        host_name = kwargs['obj'].strip('\'\"')

        if host_name not in self._hosts_list:
            return self._errors['CMMVC5753E']

        self._hosts_list[host_name]['chapsecret'] = secret
        return ('', '')

    # Remove a host
    def _cmd_rmhost(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']

        host_name = kwargs['obj'].strip('\'\"')
        if host_name not in self._hosts_list:
            return self._errors['CMMVC5753E']

        for v in self._mappings_list.values():
            if (v['host'] == host_name):
                return self._errors['CMMVC5871E']

        del self._hosts_list[host_name]
        return ('', '')

    # List information about hosts
    def _cmd_lshost(self, **kwargs):
        if 'obj' not in kwargs:
            rows = []
            rows.append(['id', 'name', 'port_count', 'iogrp_count', 'status'])

            found = False
            # Sort hosts by names to give predictable order for tests
            # depend on it.
            for host_name in sorted(self._hosts_list.keys()):
                host = self._hosts_list[host_name]
                filterstr = 'name=' + host['host_name']
                if (('filtervalue' not in kwargs) or
                        (kwargs['filtervalue'] == filterstr)):
                    rows.append([host['id'], host['host_name'], '1', '4',
                                'offline'])
                    found = True
            if found:
                return self._print_info_cmd(rows=rows, **kwargs)
            else:
                return ('', '')
        else:
            if self._next_cmd_error['lshost'] == 'missing_host':
                self._next_cmd_error['lshost'] = ''
                return self._errors['CMMVC5754E']
            elif self._next_cmd_error['lshost'] == 'bigger_troubles':
                return self._errors['CMMVC6527E']
            host_name = kwargs['obj'].strip('\'\"')
            if host_name not in self._hosts_list:
                return self._errors['CMMVC5754E']
            if (self._next_cmd_error['lshost'] == 'fail_fastpath' and
                    host_name == 'DifferentHost'):
                return self._errors['CMMVC5701E']
            host = self._hosts_list[host_name]
            rows = []
            rows.append(['id', host['id']])
            rows.append(['name', host['host_name']])
            rows.append(['port_count', '1'])
            rows.append(['type', 'generic'])
            rows.append(['mask', '1111'])
            rows.append(['iogrp_count', '4'])
            rows.append(['status', 'online'])
            for port in host['iscsi_names']:
                rows.append(['iscsi_name', port])
                rows.append(['node_logged_in_count', '0'])
                rows.append(['state', 'offline'])
            for port in host['wwpns']:
                rows.append(['WWPN', port])
                rows.append(['node_logged_in_count', '0'])
                rows.append(['state', 'active'])

            if 'nohdr' in kwargs:
                for index in range(len(rows)):
                    rows[index] = ' '.join(rows[index][1:])

            if 'delim' in kwargs:
                for index in range(len(rows)):
                    rows[index] = kwargs['delim'].join(rows[index])

            return ('%s' % '\n'.join(rows), '')

    # List iSCSI authorization information about hosts
    def _cmd_lsiscsiauth(self, **kwargs):
        if self._next_cmd_error['lsiscsiauth'] == 'no_info':
            self._next_cmd_error['lsiscsiauth'] = ''
            return ('', '')
        rows = []
        rows.append(['type', 'id', 'name', 'iscsi_auth_method',
                     'iscsi_chap_secret'])

        for host in self._hosts_list.values():
            method = 'none'
            secret = ''
            if 'chapsecret' in host:
                method = 'chap'
                secret = host['chapsecret']
            rows.append(['host', host['id'], host['host_name'], method,
                         secret])
        return self._print_info_cmd(rows=rows, **kwargs)

    # Create a vdisk-host mapping
    def _cmd_mkvdiskhostmap(self, **kwargs):
        mapping_info = {}
        mapping_info['id'] = self._find_unused_id(self._mappings_list)
        if 'host' not in kwargs:
            return self._errors['CMMVC5707E']
        mapping_info['host'] = kwargs['host'].strip('\'\"')

        if 'scsi' in kwargs:
            mapping_info['lun'] = kwargs['scsi'].strip('\'\"')
        else:
            mapping_info['lun'] = mapping_info['id']

        if 'obj' not in kwargs:
            return self._errors['CMMVC5707E']
        mapping_info['vol'] = kwargs['obj'].strip('\'\"')

        if mapping_info['vol'] not in self._volumes_list:
            return self._errors['CMMVC5753E']

        if mapping_info['host'] not in self._hosts_list:
            return self._errors['CMMVC5754E']

        if mapping_info['vol'] in self._mappings_list:
            return self._errors['CMMVC6071E']

        for v in self._mappings_list.values():
            if ((v['host'] == mapping_info['host']) and
                    (v['lun'] == mapping_info['lun'])):
                return self._errors['CMMVC5879E']

        for v in self._mappings_list.values():
            if (v['vol'] == mapping_info['vol']) and ('force' not in kwargs):
                return self._errors['CMMVC6071E']

        self._mappings_list[mapping_info['id']] = mapping_info
        return ('Virtual Disk to Host map, id [%s], successfully created'
                % (mapping_info['id']), '')

    # Delete a vdisk-host mapping
    def _cmd_rmvdiskhostmap(self, **kwargs):
        if 'host' not in kwargs:
            return self._errors['CMMVC5707E']
        host = kwargs['host'].strip('\'\"')

        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol = kwargs['obj'].strip('\'\"')

        mapping_ids = []
        for v in self._mappings_list.values():
            if v['vol'] == vol:
                mapping_ids.append(v['id'])
        if not mapping_ids:
            return self._errors['CMMVC5753E']

        this_mapping = None
        for mapping_id in mapping_ids:
            if self._mappings_list[mapping_id]['host'] == host:
                this_mapping = mapping_id
        if this_mapping is None:
            return self._errors['CMMVC5753E']

        del self._mappings_list[this_mapping]
        return ('', '')

    # List information about host->vdisk mappings
    def _cmd_lshostvdiskmap(self, **kwargs):
        host_name = kwargs['obj'].strip('\'\"')

        if host_name not in self._hosts_list:
            return self._errors['CMMVC5754E']

        rows = []
        rows.append(['id', 'name', 'SCSI_id', 'vdisk_id', 'vdisk_name',
                     'vdisk_UID'])

        for mapping in self._mappings_list.values():
            if (host_name == '') or (mapping['host'] == host_name):
                volume = self._volumes_list[mapping['vol']]
                rows.append([mapping['id'], mapping['host'],
                            mapping['lun'], volume['id'],
                            volume['name'], volume['uid']])

        return self._print_info_cmd(rows=rows, **kwargs)

    # List information about vdisk->host mappings
    def _cmd_lsvdiskhostmap(self, **kwargs):
        mappings_found = 0
        vdisk_name = kwargs['obj'].strip('\'\"')

        if vdisk_name not in self._volumes_list:
            return self._errors['CMMVC5753E']

        rows = []
        rows.append(['id name', 'SCSI_id', 'host_id', 'host_name', 'vdisk_UID',
                     'IO_group_id', 'IO_group_name'])

        for mapping in self._mappings_list.values():
            if (mapping['vol'] == vdisk_name):
                mappings_found += 1
                volume = self._volumes_list[mapping['vol']]
                host = self._hosts_list[mapping['host']]
                rows.append([volume['id'], mapping['lun'], host['id'],
                            host['host_name'], volume['uid'],
                            volume['IO_group_id'], volume['IO_group_name']])

        if mappings_found:
            return self._print_info_cmd(rows=rows, **kwargs)
        else:
            return ('', '')

    # Create a FlashCopy mapping
    def _cmd_mkfcmap(self, **kwargs):
        source = ''
        target = ''
        copyrate = kwargs['copyrate'] if 'copyrate' in kwargs else '50'

        if 'source' not in kwargs:
            return self._errors['CMMVC5707E']
        source = kwargs['source'].strip('\'\"')
        if source not in self._volumes_list:
            return self._errors['CMMVC5754E']

        if 'target' not in kwargs:
            return self._errors['CMMVC5707E']
        target = kwargs['target'].strip('\'\"')
        if target not in self._volumes_list:
            return self._errors['CMMVC5754E']

        if source == target:
            return self._errors['CMMVC6303E']

        if (self._volumes_list[source]['capacity'] !=
                self._volumes_list[target]['capacity']):
            return self._errors['CMMVC5754E']

        fcmap_info = {}
        fcmap_info['source'] = source
        fcmap_info['target'] = target
        fcmap_info['id'] = self._find_unused_id(self._fcmappings_list)
        fcmap_info['name'] = 'fcmap' + fcmap_info['id']
        fcmap_info['copyrate'] = copyrate
        fcmap_info['progress'] = '0'
        fcmap_info['autodelete'] = True if 'autodelete' in kwargs else False
        fcmap_info['status'] = 'idle_or_copied'

        # Add fcmap to consistency group
        if 'consistgrp' in kwargs:
            consistgrp = kwargs['consistgrp']

            # if is digit, assume is cg id, else is cg name
            cg_id = 0
            if not consistgrp.isdigit():
                for consistgrp_key in self._fcconsistgrp_list.keys():
                    if (self._fcconsistgrp_list[consistgrp_key]['name']
                            == consistgrp):
                        cg_id = consistgrp_key
                        fcmap_info['consistgrp'] = consistgrp_key
                        break
            else:
                if int(consistgrp) in self._fcconsistgrp_list.keys():
                    cg_id = int(consistgrp)

            # If can't find exist consistgrp id, return not exist error
            if not cg_id:
                return self._errors['CMMVC5754E']

            fcmap_info['consistgrp'] = cg_id
            # Add fcmap to consistgrp
            self._fcconsistgrp_list[cg_id]['fcmaps'][fcmap_info['id']] = (
                fcmap_info['name'])
            self._fc_cg_state_transition('add',
                                         self._fcconsistgrp_list[cg_id])

        self._fcmappings_list[fcmap_info['id']] = fcmap_info

        return('FlashCopy Mapping, id [' + fcmap_info['id'] +
               '], successfully created', '')

    def _cmd_prestartfcmap(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']

        if self._next_cmd_error['prestartfcmap'] == 'bad_id':
            id_num = -1
            self._next_cmd_error['prestartfcmap'] = ''

        try:
            fcmap = self._fcmappings_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        return self._state_transition('prepare', fcmap)

    def _cmd_startfcmap(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']

        if self._next_cmd_error['startfcmap'] == 'bad_id':
            id_num = -1
            self._next_cmd_error['startfcmap'] = ''

        try:
            fcmap = self._fcmappings_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        return self._state_transition('start', fcmap)

    def _cmd_stopfcmap(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']

        try:
            fcmap = self._fcmappings_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        return self._state_transition('stop', fcmap)

    def _cmd_rmfcmap(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']
        force = True if 'force' in kwargs else False

        if self._next_cmd_error['rmfcmap'] == 'bad_id':
            id_num = -1
            self._next_cmd_error['rmfcmap'] = ''

        try:
            fcmap = self._fcmappings_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        function = 'delete_force' if force else 'delete'
        ret = self._state_transition(function, fcmap)
        if fcmap['status'] == 'end':
            del self._fcmappings_list[id_num]
        return ret

    def _cmd_lsvdiskfcmappings(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5707E']
        vdisk = kwargs['obj']
        rows = []
        rows.append(['id', 'name'])
        for v in self._fcmappings_list.values():
            if v['source'] == vdisk or v['target'] == vdisk:
                rows.append([v['id'], v['name']])
        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_chfcmap(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5707E']
        id_num = kwargs['obj']

        try:
            fcmap = self._fcmappings_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        for key in ['name', 'copyrate', 'autodelete']:
            if key in kwargs:
                fcmap[key] = kwargs[key]
        return ('', '')

    def _cmd_lsfcmap(self, **kwargs):
        rows = []
        rows.append(['id', 'name', 'source_vdisk_id', 'source_vdisk_name',
                     'target_vdisk_id', 'target_vdisk_name', 'group_id',
                     'group_name', 'status', 'progress', 'copy_rate',
                     'clean_progress', 'incremental', 'partner_FC_id',
                     'partner_FC_name', 'restoring', 'start_time',
                     'rc_controlled'])

        # Assume we always get a filtervalue argument
        filter_key = kwargs['filtervalue'].split('=')[0]
        filter_value = kwargs['filtervalue'].split('=')[1]
        to_delete = []
        for k, v in self._fcmappings_list.items():
            if six.text_type(v[filter_key]) == filter_value:
                source = self._volumes_list[v['source']]
                target = self._volumes_list[v['target']]
                self._state_transition('wait', v)

                if self._next_cmd_error['lsfcmap'] == 'speed_up':
                    self._next_cmd_error['lsfcmap'] = ''
                    curr_state = v['status']
                    while self._state_transition('wait', v) == ("", ""):
                        if curr_state == v['status']:
                            break
                        curr_state = v['status']

                if ((v['status'] == 'idle_or_copied' and v['autodelete'] and
                     v['progress'] == '100') or (v['status'] == 'end')):
                    to_delete.append(k)
                else:
                    rows.append([v['id'], v['name'], source['id'],
                                 source['name'], target['id'], target['name'],
                                 '', '', v['status'], v['progress'],
                                 v['copyrate'], '100', 'off', '', '', 'no', '',
                                 'no'])

        for d in to_delete:
            del self._fcmappings_list[d]

        return self._print_info_cmd(rows=rows, **kwargs)

    # Create a FlashCopy mapping
    def _cmd_mkfcconsistgrp(self, **kwargs):
        fcconsistgrp_info = {}
        fcconsistgrp_info['id'] = self._find_unused_id(self._fcconsistgrp_list)

        if 'name' in kwargs:
            fcconsistgrp_info['name'] = kwargs['name'].strip('\'\"')
        else:
            fcconsistgrp_info['name'] = 'fccstgrp' + fcconsistgrp_info['id']

        if 'autodelete' in kwargs:
            fcconsistgrp_info['autodelete'] = True
        else:
            fcconsistgrp_info['autodelete'] = False
        fcconsistgrp_info['status'] = 'empty'
        fcconsistgrp_info['start_time'] = None
        fcconsistgrp_info['fcmaps'] = {}

        self._fcconsistgrp_list[fcconsistgrp_info['id']] = fcconsistgrp_info

        return('FlashCopy Consistency Group, id [' + fcconsistgrp_info['id'] +
               '], successfully created', '')

    def _cmd_prestartfcconsistgrp(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        cg_name = kwargs['obj']

        cg_id = 0
        for cg_id in self._fcconsistgrp_list.keys():
            if cg_name == self._fcconsistgrp_list[cg_id]['name']:
                break

        return self._fc_cg_state_transition('prepare',
                                            self._fcconsistgrp_list[cg_id])

    def _cmd_startfcconsistgrp(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        cg_name = kwargs['obj']

        cg_id = 0
        for cg_id in self._fcconsistgrp_list.keys():
            if cg_name == self._fcconsistgrp_list[cg_id]['name']:
                break

        return self._fc_cg_state_transition('start',
                                            self._fcconsistgrp_list[cg_id])

    def _cmd_stopfcconsistgrp(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']

        try:
            fcconsistgrps = self._fcconsistgrp_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        return self._fc_cg_state_transition('stop', fcconsistgrps)

    def _cmd_rmfcconsistgrp(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        cg_name = kwargs['obj']
        force = True if 'force' in kwargs else False

        cg_id = 0
        for cg_id in self._fcconsistgrp_list.keys():
            if cg_name == self._fcconsistgrp_list[cg_id]['name']:
                break
        if not cg_id:
            return self._errors['CMMVC5753E']
        fcconsistgrps = self._fcconsistgrp_list[cg_id]

        function = 'delete_force' if force else 'delete'
        ret = self._fc_cg_state_transition(function, fcconsistgrps)
        if fcconsistgrps['status'] == 'end':
            del self._fcconsistgrp_list[cg_id]
        return ret

    def _cmd_lsfcconsistgrp(self, **kwargs):
        rows = []

        if 'obj' not in kwargs:
            rows.append(['id', 'name', 'status' 'start_time'])

            for fcconsistgrp in self._fcconsistgrp_list.values():
                rows.append([fcconsistgrp['id'],
                             fcconsistgrp['name'],
                             fcconsistgrp['status'],
                             fcconsistgrp['start_time']])
            return self._print_info_cmd(rows=rows, **kwargs)
        else:
            fcconsistgrp = None
            cg_id = 0
            for cg_id in self._fcconsistgrp_list.keys():
                if self._fcconsistgrp_list[cg_id]['name'] == kwargs['obj']:
                    fcconsistgrp = self._fcconsistgrp_list[cg_id]
            rows = []
            rows.append(['id', six.text_type(cg_id)])
            rows.append(['name', fcconsistgrp['name']])
            rows.append(['status', fcconsistgrp['status']])
            rows.append(['autodelete',
                         six.text_type(fcconsistgrp['autodelete'])])
            rows.append(['start_time',
                         six.text_type(fcconsistgrp['start_time'])])

            for fcmap_id in fcconsistgrp['fcmaps'].keys():
                rows.append(['FC_mapping_id', six.text_type(fcmap_id)])
                rows.append(['FC_mapping_name',
                             fcconsistgrp['fcmaps'][fcmap_id]])

            if 'delim' in kwargs:
                for index in range(len(rows)):
                    rows[index] = kwargs['delim'].join(rows[index])
            self._fc_cg_state_transition('wait', fcconsistgrp)
            return ('%s' % '\n'.join(rows), '')

    def _cmd_migratevdisk(self, **kwargs):
        if 'mdiskgrp' not in kwargs or 'vdisk' not in kwargs:
            return self._errors['CMMVC5707E']
        mdiskgrp = kwargs['mdiskgrp'].strip('\'\"')
        vdisk = kwargs['vdisk'].strip('\'\"')
        copy_id = kwargs['copy']
        if vdisk not in self._volumes_list:
            return self._errors['CMMVC5753E']
        mdiskgrp_id = str(self._get_mdiskgrp_id(mdiskgrp))

        self._volumes_list[vdisk]['mdisk_grp_name'] = mdiskgrp
        self._volumes_list[vdisk]['mdisk_grp_id'] = mdiskgrp_id

        vol = self._volumes_list[vdisk]
        vol['copies'][copy_id]['mdisk_grp_name'] = mdiskgrp
        vol['copies'][copy_id]['mdisk_grp_id'] = mdiskgrp_id
        return ('', '')

    def _cmd_addvdiskcopy(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')
        if vol_name not in self._volumes_list:
            return self._errors['CMMVC5753E']
        vol = self._volumes_list[vol_name]
        if 'mdiskgrp' not in kwargs:
            return self._errors['CMMVC5707E']
        mdiskgrp = kwargs['mdiskgrp'].strip('\'\"')
        if mdiskgrp == kwargs['mdiskgrp']:
            raise exception.InvalidInput(
                reason=_('mdiskgrp missing quotes %s') % kwargs['mdiskgrp'])
        auto_del = True if 'autodelete' in kwargs else False

        copy_info = {}
        copy_info['id'] = self._find_unused_id(vol['copies'])
        copy_info['status'] = 'online'
        copy_info['sync'] = 'no'
        copy_info['primary'] = 'no'
        copy_info['mdisk_grp_name'] = mdiskgrp
        copy_info['mdisk_grp_id'] = str(self._get_mdiskgrp_id(mdiskgrp))

        if 'easytier' in kwargs:
            if kwargs['easytier'] == 'on':
                copy_info['easy_tier'] = 'on'
            else:
                copy_info['easy_tier'] = 'off'
        if 'rsize' in kwargs:
            if 'compressed' in kwargs:
                copy_info['compressed_copy'] = 'yes'
            else:
                copy_info['compressed_copy'] = 'no'
        vol['copies'][copy_info['id']] = copy_info
        if auto_del:
            del_copy_id = None
            for v in vol['copies'].values():
                if v['id'] != copy_info['id']:
                    del_copy_id = v['id']
                    break
            if del_copy_id:
                del vol['copies'][del_copy_id]
        return ('Vdisk [%(vid)s] copy [%(cid)s] successfully created' %
                {'vid': vol['id'], 'cid': copy_info['id']}, '')

    def _cmd_lsvdiskcopy(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5804E']
        name = kwargs['obj']
        vol = self._volumes_list[name]
        rows = []
        rows.append(['vdisk_id', 'vdisk_name', 'copy_id', 'status', 'sync',
                     'primary', 'mdisk_grp_id', 'mdisk_grp_name', 'capacity',
                     'type', 'se_copy', 'easy_tier', 'easy_tier_status',
                     'compressed_copy'])
        for copy in vol['copies'].values():
            rows.append([vol['id'], vol['name'], copy['id'],
                        copy['status'], copy['sync'], copy['primary'],
                        copy['mdisk_grp_id'], copy['mdisk_grp_name'],
                        vol['capacity'], 'striped', 'yes', copy['easy_tier'],
                        'inactive', copy['compressed_copy']])
        if 'copy' not in kwargs:
            return self._print_info_cmd(rows=rows, **kwargs)
        else:
            copy_id = kwargs['copy'].strip('\'\"')
            if copy_id not in vol['copies']:
                return self._errors['CMMVC6353E']
            copy = vol['copies'][copy_id]
            rows = []
            rows.append(['vdisk_id', vol['id']])
            rows.append(['vdisk_name', vol['name']])
            rows.append(['capacity', vol['capacity']])
            rows.append(['copy_id', copy['id']])
            rows.append(['status', copy['status']])
            rows.append(['sync', copy['sync']])
            copy['sync'] = 'yes'
            rows.append(['primary', copy['primary']])
            rows.append(['mdisk_grp_id', copy['mdisk_grp_id']])
            rows.append(['mdisk_grp_name', copy['mdisk_grp_name']])
            rows.append(['easy_tier', copy['easy_tier']])
            rows.append(['easy_tier_status', 'inactive'])
            rows.append(['compressed_copy', copy['compressed_copy']])
            rows.append(['autoexpand', vol['autoexpand']])

            if 'delim' in kwargs:
                for index in range(len(rows)):
                    rows[index] = kwargs['delim'].join(rows[index])

            return ('%s' % '\n'.join(rows), '')

    def _cmd_rmvdiskcopy(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')
        if 'copy' not in kwargs:
            return self._errors['CMMVC5707E']
        copy_id = kwargs['copy'].strip('\'\"')
        if vol_name not in self._volumes_list:
            return self._errors['CMMVC5753E']
        vol = self._volumes_list[vol_name]
        if copy_id not in vol['copies']:
            return self._errors['CMMVC6353E']
        del vol['copies'][copy_id]

        return ('', '')

    def _cmd_chvdisk(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')
        vol = self._volumes_list[vol_name]
        kwargs.pop('obj')

        params = ['name', 'warning', 'udid',
                  'autoexpand', 'easytier', 'primary']
        for key, value in kwargs.items():
            if key == 'easytier':
                vol['easy_tier'] = value
                for copy in vol['copies'].values():
                    vol['copies'][copy['id']]['easy_tier'] = value
                continue
            if key == 'warning':
                vol['warning'] = value.rstrip('%')
                continue
            if key == 'name':
                vol['name'] = value
                del self._volumes_list[vol_name]
                self._volumes_list[value] = vol
            if key == 'primary':
                if value == '0':
                    self._volumes_list[vol_name]['copies']['0']['primary']\
                        = 'yes'
                    self._volumes_list[vol_name]['copies']['1']['primary']\
                        = 'no'
                elif value == '1':
                    self._volumes_list[vol_name]['copies']['0']['primary']\
                        = 'no'
                    self._volumes_list[vol_name]['copies']['1']['primary']\
                        = 'yes'
                else:
                    err = self._errors['CMMVC6353E'][1] % {'VALUE': key}
                    return ('', err)
            if key in params:
                vol[key] = value
                if key == 'autoexpand':
                    for copy in vol['copies'].values():
                        vol['copies'][copy['id']]['autoexpand'] = value
            else:
                err = self._errors['CMMVC5709E'][1] % {'VALUE': key}
                return ('', err)
        return ('', '')

    def _cmd_movevdisk(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        vol_name = kwargs['obj'].strip('\'\"')
        vol = self._volumes_list[vol_name]

        if 'iogrp' not in kwargs:
            return self._errors['CMMVC5707E']

        iogrp = kwargs['iogrp']
        if iogrp.isdigit():
            vol['IO_group_id'] = iogrp
            vol['IO_group_name'] = 'io_grp%s' % iogrp
        else:
            vol['IO_group_id'] = iogrp[6:]
            vol['IO_group_name'] = iogrp
        return ('', '')

    def _cmd_addvdiskaccess(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        return ('', '')

    def _cmd_rmvdiskaccess(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        return ('', '')

    # list vdisk sync process
    def _cmd_lsvdisksyncprogress(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5804E']
        name = kwargs['obj']
        copy_id = kwargs.get('copy', None)
        vol = self._volumes_list[name]
        rows = []
        rows.append(['vdisk_id', 'vdisk_name', 'copy_id', 'progress',
                     'estimated_completion_time'])
        copy_found = False
        for copy in vol['copies'].values():
            if not copy_id or copy_id == copy['id']:
                copy_found = True
                row = [vol['id'], name, copy['id']]
                if copy['sync'] == 'yes':
                    row.extend(['100', ''])
                else:
                    row.extend(['50', '140210115226'])
                    copy['sync'] = 'yes'
                rows.append(row)
        if not copy_found:
            return self._errors['CMMVC5804E']
        return self._print_info_cmd(rows=rows, **kwargs)

    def _add_host_to_list(self, connector):
        host_info = {}
        host_info['id'] = self._find_unused_id(self._hosts_list)
        host_info['host_name'] = connector['host']
        host_info['iscsi_names'] = []
        host_info['wwpns'] = []
        if 'initiator' in connector:
            host_info['iscsi_names'].append(connector['initiator'])
        if 'wwpns' in connector:
            host_info['wwpns'] = host_info['wwpns'] + connector['wwpns']
        self._hosts_list[connector['host']] = host_info

    def _host_in_list(self, host_name):
        for k in self._hosts_list:
            if k.startswith(host_name):
                return k
        return None

    # Replication related command
    # Create a remote copy
    def _cmd_mkrcrelationship(self, **kwargs):
        master_vol = ''
        aux_vol = ''
        aux_cluster = ''
        master_sys = self._system_list['storwize-svc-sim']
        aux_sys = self._system_list['aux-svc-sim']

        if 'master' not in kwargs:
            return self._errors['CMMVC5707E']
        master_vol = kwargs['master'].strip('\'\"')
        if master_vol not in self._volumes_list:
            return self._errors['CMMVC5754E']

        if 'aux' not in kwargs:
            return self._errors['CMMVC5707E']
        aux_vol = kwargs['aux'].strip('\'\"')
        if aux_vol not in self._volumes_list:
            return self._errors['CMMVC5754E']

        if 'cluster' not in kwargs:
            return self._errors['CMMVC5707E']
        aux_cluster = kwargs['cluster'].strip('\'\"')
        if aux_cluster != aux_sys['name']:
            return self._errors['CMMVC5754E']

        if (self._volumes_list[master_vol]['capacity'] !=
                self._volumes_list[aux_vol]['capacity']):
            return self._errors['CMMVC5754E']
        rcrel_info = {}
        rcrel_info['id'] = self._find_unused_id(self._rcrelationship_list)
        rcrel_info['name'] = 'rcrel' + rcrel_info['id']
        rcrel_info['master_cluster_id'] = master_sys['id']
        rcrel_info['master_cluster_name'] = master_sys['name']
        rcrel_info['master_vdisk_id'] = self._volumes_list[master_vol]['id']
        rcrel_info['master_vdisk_name'] = master_vol
        rcrel_info['aux_cluster_id'] = aux_sys['id']
        rcrel_info['aux_cluster_name'] = aux_sys['name']
        rcrel_info['aux_vdisk_id'] = self._volumes_list[aux_vol]['id']
        rcrel_info['aux_vdisk_name'] = aux_vol
        rcrel_info['primary'] = 'master'
        rcrel_info['consistency_group_id'] = ''
        rcrel_info['consistency_group_name'] = ''
        rcrel_info['state'] = 'inconsistent_stopped'
        rcrel_info['bg_copy_priority'] = '50'
        rcrel_info['progress'] = '0'
        rcrel_info['freeze_time'] = ''
        rcrel_info['status'] = 'online'
        rcrel_info['sync'] = ''
        rcrel_info['copy_type'] = 'global' if 'global' in kwargs else 'metro'
        rcrel_info['cycling_mode'] = ''
        rcrel_info['cycle_period_seconds'] = '300'
        rcrel_info['master_change_vdisk_id'] = ''
        rcrel_info['master_change_vdisk_name'] = ''
        rcrel_info['aux_change_vdisk_id'] = ''
        rcrel_info['aux_change_vdisk_name'] = ''

        self._rcrelationship_list[rcrel_info['name']] = rcrel_info
        self._volumes_list[master_vol]['RC_name'] = rcrel_info['name']
        self._volumes_list[master_vol]['RC_id'] = rcrel_info['id']
        self._volumes_list[aux_vol]['RC_name'] = rcrel_info['name']
        self._volumes_list[aux_vol]['RC_id'] = rcrel_info['id']
        return('RC Relationship, id [' + rcrel_info['id'] +
               '], successfully created', '')

    def _cmd_lsrcrelationship(self, **kwargs):
        rows = []
        rows.append(['id', 'name', 'master_cluster_id', 'master_cluster_name',
                     'master_vdisk_id', 'master_vdisk_name', 'aux_cluster_id',
                     'aux_cluster_name', 'aux_vdisk_id', 'aux_vdisk_name',
                     'consistency_group_id', 'primary',
                     'consistency_group_name', 'state', 'bg_copy_priority',
                     'progress', 'freeze_time', 'status', 'sync',
                     'copy_type', 'cycling_mode', 'cycle_period_seconds',
                     'master_change_vdisk_id', 'master_change_vdisk_name',
                     'aux_change_vdisk_id', 'aux_change_vdisk_name'])

        # Assume we always get a filtervalue argument
        filter_key = kwargs['filtervalue'].split('=')[0]
        filter_value = kwargs['filtervalue'].split('=')[1]
        for k, v in self._rcrelationship_list.items():
            if six.text_type(v[filter_key]) == filter_value:
                self._rc_state_transition('wait', v)

                if self._next_cmd_error['lsrcrelationship'] == 'speed_up':
                    self._next_cmd_error['lsrcrelationship'] = ''
                    curr_state = v['status']
                    while self._rc_state_transition('wait', v) == ("", ""):
                        if curr_state == v['status']:
                            break
                        curr_state = v['status']

                rows.append([v['id'], v['name'], v['master_cluster_id'],
                             v['master_cluster_name'], v['master_vdisk_id'],
                             v['master_vdisk_name'], v['aux_cluster_id'],
                             v['aux_cluster_name'], v['aux_vdisk_id'],
                             v['aux_vdisk_name'], v['consistency_group_id'],
                             v['primary'], v['consistency_group_name'],
                             v['state'], v['bg_copy_priority'], v['progress'],
                             v['freeze_time'], v['status'], v['sync'],
                             v['copy_type'], v['cycling_mode'],
                             v['cycle_period_seconds'],
                             v['master_change_vdisk_id'],
                             v['master_change_vdisk_name'],
                             v['aux_change_vdisk_id'],
                             v['aux_change_vdisk_name']])

        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_startrcrelationship(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']

        primary_vol = None
        if 'primary' in kwargs:
            primary_vol = kwargs['primary'].strip('\'\"')

        try:
            rcrel = self._rcrelationship_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        if rcrel['state'] == 'idling' and not primary_vol:
            return self._errors['CMMVC5963E']

        self._rc_state_transition('start', rcrel)
        if primary_vol:
            self._rcrelationship_list[id_num]['primary'] = primary_vol
        return ('', '')

    def _cmd_stoprcrelationship(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']
        force_access = True if 'access' in kwargs else False

        try:
            rcrel = self._rcrelationship_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        function = 'stop_access' if force_access else 'stop'
        self._rc_state_transition(function, rcrel)
        if force_access:
            self._rcrelationship_list[id_num]['primary'] = ''
        return ('', '')

    def _cmd_switchrcrelationship(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5707E']
        id_num = kwargs['obj']

        try:
            rcrel = self._rcrelationship_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        if rcrel['state'] == storwize_const.REP_CONSIS_SYNC:
            rcrel['primary'] = kwargs['primary']
            return ('', '')
        else:
            return self._errors['CMMVC5753E']

    def _cmd_rmrcrelationship(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        id_num = kwargs['obj']
        force = True if 'force' in kwargs else False

        try:
            rcrel = self._rcrelationship_list[id_num]
        except KeyError:
            return self._errors['CMMVC5753E']

        function = 'delete_force' if force else 'delete'
        self._rc_state_transition(function, rcrel)
        if rcrel['state'] == 'end':
            self._volumes_list[rcrel['master_vdisk_name']]['RC_name'] = ''
            self._volumes_list[rcrel['master_vdisk_name']]['RC_id'] = ''
            self._volumes_list[rcrel['aux_vdisk_name']]['RC_name'] = ''
            self._volumes_list[rcrel['aux_vdisk_name']]['RC_id'] = ''
            del self._rcrelationship_list[id_num]

        return ('', '')

    def _rc_state_transition(self, function, rcrel):
        if (function == 'wait' and
                'wait' not in self._rc_transitions[rcrel['state']]):
            return ('', '')

        if rcrel['state'] == 'inconsistent_copying' and function == 'wait':
            if rcrel['progress'] == '0':
                rcrel['progress'] = '50'
            else:
                rcrel['progress'] = '100'
                rcrel['state'] = 'consistent_synchronized'
            return ('', '')
        else:
            try:
                curr_state = rcrel['state']
                rcrel['state'] = self._rc_transitions[curr_state][function]
                return ('', '')
            except Exception:
                return self._errors['CMMVC5982E']

    def _cmd_lspartnershipcandidate(self, **kwargs):
        rows = [None] * 4
        master_sys = self._system_list['storwize-svc-sim']
        aux_sys = self._system_list['aux-svc-sim']
        rows[0] = ['id', 'configured', 'name']
        rows[1] = [master_sys['id'], 'no', master_sys['name']]
        rows[2] = [aux_sys['id'], 'no', aux_sys['name']]
        rows[3] = ['0123456789001234', 'no', 'fake_svc']
        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_lspartnership(self, **kwargs):
        rows = []
        rows.append(['id', 'name', 'location', 'partnership',
                     'type', 'cluster_ip', 'event_log_sequence'])

        master_sys = self._system_list['storwize-svc-sim']
        if master_sys['name'] not in self._partnership_list:
            local_info = {}
            local_info['id'] = master_sys['id']
            local_info['name'] = master_sys['name']
            local_info['location'] = 'local'
            local_info['type'] = ''
            local_info['cluster_ip'] = ''
            local_info['event_log_sequence'] = ''
            local_info['chap_secret'] = ''
            local_info['linkbandwidthmbits'] = ''
            local_info['backgroundcopyrate'] = ''
            local_info['partnership'] = ''
            self._partnership_list[master_sys['id']] = local_info

        # Assume we always get a filtervalue argument
        filter_key = kwargs['filtervalue'].split('=')[0]
        filter_value = kwargs['filtervalue'].split('=')[1]
        for k, v in self._partnership_list.items():
            if six.text_type(v[filter_key]) == filter_value:
                rows.append([v['id'], v['name'], v['location'],
                             v['partnership'], v['type'], v['cluster_ip'],
                             v['event_log_sequence']])
        return self._print_info_cmd(rows=rows, **kwargs)

    def _cmd_mkippartnership(self, **kwargs):
        if 'clusterip' not in kwargs:
            return self._errors['CMMVC5707E']
        clusterip = kwargs['master'].strip('\'\"')

        if 'linkbandwidthmbits' not in kwargs:
            return self._errors['CMMVC5707E']
        bandwith = kwargs['linkbandwidthmbits'].strip('\'\"')

        if 'backgroundcopyrate' not in kwargs:
            return self._errors['CMMVC5707E']
        copyrate = kwargs['backgroundcopyrate'].strip('\'\"')

        if clusterip == '192.168.10.21':
            partner_info_id = self._system_list['storwize-svc-sim']['id']
            partner_info_name = self._system_list['storwize-svc-sim']['name']
        else:
            partner_info_id = self._system_list['aux-svc-sim']['id']
            partner_info_name = self._system_list['aux-svc-sim']['name']

        partner_info = {}
        partner_info['id'] = partner_info_id
        partner_info['name'] = partner_info_name
        partner_info['location'] = 'remote'
        partner_info['type'] = 'ipv4'
        partner_info['cluster_ip'] = clusterip
        partner_info['event_log_sequence'] = ''
        partner_info['chap_secret'] = ''
        partner_info['linkbandwidthmbits'] = bandwith
        partner_info['backgroundcopyrate'] = copyrate
        partner_info['partnership'] = 'fully_configured'

        self._partnership_list[partner_info['id']] = partner_info
        return('', '')

    def _cmd_mkfcpartnership(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        peer_sys = kwargs['obj']

        if 'linkbandwidthmbits' not in kwargs:
            return self._errors['CMMVC5707E']
        bandwith = kwargs['linkbandwidthmbits'].strip('\'\"')

        if 'backgroundcopyrate' not in kwargs:
            return self._errors['CMMVC5707E']
        copyrate = kwargs['backgroundcopyrate'].strip('\'\"')

        partner_info = {}
        partner_info['id'] = self._system_list[peer_sys]['id']
        partner_info['name'] = peer_sys
        partner_info['location'] = 'remote'
        partner_info['type'] = 'fc'
        partner_info['cluster_ip'] = ''
        partner_info['event_log_sequence'] = ''
        partner_info['chap_secret'] = ''
        partner_info['linkbandwidthmbits'] = bandwith
        partner_info['backgroundcopyrate'] = copyrate
        partner_info['partnership'] = 'fully_configured'
        self._partnership_list[partner_info['id']] = partner_info
        return('', '')

    def _cmd_chpartnership(self, **kwargs):
        if 'obj' not in kwargs:
            return self._errors['CMMVC5701E']
        peer_sys = kwargs['obj']
        if peer_sys not in self._partnership_list:
            return self._errors['CMMVC5753E']

        partner_state = ('fully_configured' if 'start'in kwargs
                         else 'fully_configured_stopped')
        self._partnership_list[peer_sys]['partnership'] = partner_state
        return('', '')

    # The main function to run commands on the management simulator
    def execute_command(self, cmd, check_exit_code=True):
        try:
            kwargs = self._cmd_to_dict(cmd)
        except IndexError:
            return self._errors['CMMVC5707E']

        command = kwargs.pop('cmd')
        func = getattr(self, '_cmd_' + command)
        out, err = func(**kwargs)

        if (check_exit_code) and (len(err) != 0):
            raise processutils.ProcessExecutionError(exit_code=1,
                                                     stdout=out,
                                                     stderr=err,
                                                     cmd=' '.join(cmd))

        return (out, err)

    # After calling this function, the next call to the specified command will
    # result in in the error specified
    def error_injection(self, cmd, error):
        self._next_cmd_error[cmd] = error

    def change_vdiskcopy_attr(self, vol_name, key, value, copy="primary"):
        if copy == 'primary':
            self._volumes_list[vol_name]['copies']['0'][key] = value
        elif copy == 'secondary':
            self._volumes_list[vol_name]['copies']['1'][key] = value
        else:
            msg = _("The copy should be primary or secondary")
            raise exception.InvalidInput(reason=msg)


class StorwizeSVCISCSIFakeDriver(storwize_svc_iscsi.StorwizeSVCISCSIDriver):
    def __init__(self, *args, **kwargs):
        super(StorwizeSVCISCSIFakeDriver, self).__init__(*args, **kwargs)

    def set_fake_storage(self, fake):
        self.fake_storage = fake

    def _run_ssh(self, cmd, check_exit_code=True, attempts=1):
        utils.check_ssh_injection(cmd)
        ret = self.fake_storage.execute_command(cmd, check_exit_code)

        return ret


class StorwizeSVCFcFakeDriver(storwize_svc_fc.StorwizeSVCFCDriver):
    def __init__(self, *args, **kwargs):
        super(StorwizeSVCFcFakeDriver, self).__init__(*args, **kwargs)

    def set_fake_storage(self, fake):
        self.fake_storage = fake

    def _run_ssh(self, cmd, check_exit_code=True, attempts=1):
        utils.check_ssh_injection(cmd)
        ret = self.fake_storage.execute_command(cmd, check_exit_code)

        return ret


class StorwizeSVCISCSIDriverTestCase(test.TestCase):
    @mock.patch.object(time, 'sleep')
    def setUp(self, mock_sleep):
        super(StorwizeSVCISCSIDriverTestCase, self).setUp()
        self.USESIM = True
        if self.USESIM:
            self.iscsi_driver = StorwizeSVCISCSIFakeDriver(
                configuration=conf.Configuration(None))
            self._def_flags = {'san_ip': 'hostname',
                               'san_login': 'user',
                               'san_password': 'pass',
                               'storwize_svc_volpool_name': ['openstack'],
                               'storwize_svc_flashcopy_timeout': 20,
                               'storwize_svc_flashcopy_rate': 49,
                               'storwize_svc_multipath_enabled': False,
                               'storwize_svc_allow_tenant_qos': True}
            wwpns = [
                six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
            initiator = 'test.initiator.%s' % six.text_type(
                random.randint(10000, 99999))
            self._connector = {'ip': '1.234.56.78',
                               'host': 'storwize-svc-test',
                               'wwpns': wwpns,
                               'initiator': initiator}
            self.sim = StorwizeSVCManagementSimulator(['openstack'])

            self.iscsi_driver.set_fake_storage(self.sim)
            self.ctxt = context.get_admin_context()

        self._reset_flags()
        self.ctxt = context.get_admin_context()
        db_driver = self.iscsi_driver.configuration.db_driver
        self.db = importutils.import_module(db_driver)
        self.iscsi_driver.db = self.db
        self.iscsi_driver.do_setup(None)
        self.iscsi_driver.check_for_setup_error()
        self.iscsi_driver._helpers.check_fcmapping_interval = 0

    def _set_flag(self, flag, value):
        group = self.iscsi_driver.configuration.config_group
        self.iscsi_driver.configuration.set_override(flag, value, group)

    def _reset_flags(self):
        self.iscsi_driver.configuration.local_conf.reset()
        for k, v in self._def_flags.items():
            self._set_flag(k, v)

    def _create_volume(self, **kwargs):
        pool = _get_test_pool()
        prop = {'host': 'openstack@svc#%s' % pool,
                'size': 1}
        for p in prop.keys():
            if p not in kwargs:
                kwargs[p] = prop[p]
        vol = testutils.create_volume(self.ctxt, **kwargs)
        self.iscsi_driver.create_volume(vol)
        return vol

    def _delete_volume(self, volume):
        self.iscsi_driver.delete_volume(volume)
        self.db.volume_destroy(self.ctxt, volume['id'])

    def _generate_vol_info(self, vol_name, vol_id):
        pool = _get_test_pool()
        prop = {'mdisk_grp_name': pool}
        if vol_name:
            prop.update(volume_name=vol_name,
                        volume_id=vol_id,
                        volume_size=10)
        else:
            prop.update(size=10,
                        volume_type_id=None,
                        mdisk_grp_name=pool,
                        host='openstack@svc#%s' % pool)
        vol = testutils.create_volume(self.ctxt, **prop)
        return vol

    def _assert_vol_exists(self, name, exists):
        is_vol_defined = self.iscsi_driver._helpers.is_vdisk_defined(name)
        self.assertEqual(exists, is_vol_defined)

    def test_storwize_svc_iscsi_validate_connector(self):
        conn_neither = {'host': 'host'}
        conn_iscsi = {'host': 'host', 'initiator': 'foo'}
        conn_fc = {'host': 'host', 'wwpns': 'bar'}
        conn_both = {'host': 'host', 'initiator': 'foo', 'wwpns': 'bar'}

        self.iscsi_driver._state['enabled_protocols'] = set(['iSCSI'])
        self.iscsi_driver.validate_connector(conn_iscsi)
        self.iscsi_driver.validate_connector(conn_both)
        self.assertRaises(exception.InvalidConnectorException,
                          self.iscsi_driver.validate_connector, conn_fc)
        self.assertRaises(exception.InvalidConnectorException,
                          self.iscsi_driver.validate_connector, conn_neither)

        self.iscsi_driver._state['enabled_protocols'] = set(['iSCSI', 'FC'])
        self.iscsi_driver.validate_connector(conn_iscsi)
        self.iscsi_driver.validate_connector(conn_both)
        self.assertRaises(exception.InvalidConnectorException,
                          self.iscsi_driver.validate_connector, conn_neither)

    def test_storwize_terminate_iscsi_connection(self):
        # create a iSCSI volume
        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.iscsi_driver.initialize_connection(volume_iSCSI, connector)
        self.iscsi_driver.terminate_connection(volume_iSCSI, connector)

    @mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                       '_do_terminate_connection')
    @mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                       '_do_initialize_connection')
    def test_storwize_do_terminate_iscsi_connection(self, init_conn,
                                                    term_conn):
        # create a iSCSI volume
        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.iscsi_driver.initialize_connection(volume_iSCSI, connector)
        self.iscsi_driver.terminate_connection(volume_iSCSI, connector)
        init_conn.assert_called_once_with(volume_iSCSI, connector)
        term_conn.assert_called_once_with(volume_iSCSI, connector)

    @mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                       '_do_terminate_connection')
    def test_storwize_initialize_iscsi_connection_failure(self, term_conn):
        # create a iSCSI volume
        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.iscsi_driver._state['storage_nodes'] = {}
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.iscsi_driver.initialize_connection,
                          volume_iSCSI, connector)
        term_conn.assert_called_once_with(volume_iSCSI, connector)

    def test_storwize_terminate_iscsi_connection_multi_attach(self):
        # create a iSCSI volume
        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}
        connector2 = {'host': 'STORWIZE-SVC-HOST',
                      'wwnns': ['30000090fa17311e', '30000090fa17311f'],
                      'wwpns': ['ffff000000000000', 'ffff000000000001'],
                      'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1bbb'}

        # map and unmap the volume to two hosts normal case
        self.iscsi_driver.initialize_connection(volume_iSCSI, connector)
        self.iscsi_driver.initialize_connection(volume_iSCSI, connector2)
        for conn in [connector, connector2]:
            host = self.iscsi_driver._helpers.get_host_from_connector(
                conn, iscsi=True)
            self.assertIsNotNone(host)
        self.iscsi_driver.terminate_connection(volume_iSCSI, connector)
        self.iscsi_driver.terminate_connection(volume_iSCSI, connector2)
        # validate that the host entries are deleted
        for conn in [connector, connector2]:
            host = self.iscsi_driver._helpers.get_host_from_connector(conn)
            self.assertIsNone(host)
        # map and unmap the volume to two hosts with the mapping removed
        self.iscsi_driver.initialize_connection(volume_iSCSI, connector)
        self.iscsi_driver.initialize_connection(volume_iSCSI, connector2)
        # Test multiple attachments case
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            connector2, iscsi=True)
        self.iscsi_driver._helpers.unmap_vol_from_host(
            volume_iSCSI['name'], host_name)
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            connector2, iscsi=True)
        self.assertIsNotNone(host_name)
        with mock.patch.object(storwize_svc_common.StorwizeSSH,
                               'rmvdiskhostmap') as rmmap:
            rmmap.side_effect = Exception('boom')
            self.iscsi_driver.terminate_connection(volume_iSCSI,
                                                   connector2)
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            connector2, iscsi=True)
        self.assertIsNone(host_name)
        # Test single attachment case
        self.iscsi_driver._helpers.unmap_vol_from_host(
            volume_iSCSI['name'], host_name)
        with mock.patch.object(storwize_svc_common.StorwizeSSH,
                               'rmvdiskhostmap') as rmmap:
            rmmap.side_effect = Exception('boom')
            self.iscsi_driver.terminate_connection(volume_iSCSI, connector)
        # validate that the host entries are deleted
        for conn in [connector, connector2]:
            host = self.iscsi_driver._helpers.get_host_from_connector(
                conn, iscsi=True)
        self.assertIsNone(host)

    def test_storwize_initialize_iscsi_connection_single_path(self):
        # Test the return value for _get_iscsi_properties

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}
        # Expected single path host-volume map return value
        exp_s_path = {'driver_volume_type': 'iscsi',
                      'data': {'target_discovered': False,
                               'target_iqn':
                                   'iqn.1982-01.com.ibm:1234.sim.node1',
                               'target_portal': '1.234.56.78:3260',
                               'target_lun': 0,
                               'auth_method': 'CHAP',
                               'discovery_auth_method': 'CHAP'}}

        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        # Make sure that the volumes have been created
        self._assert_vol_exists(volume_iSCSI['name'], True)

        # Check case where no hosts exist
        ret = self.iscsi_driver._helpers.get_host_from_connector(
            connector, iscsi=True)
        self.assertIsNone(ret)

        # Initialize connection to map volume to a host
        ret = self.iscsi_driver.initialize_connection(
            volume_iSCSI, connector)
        self.assertEqual(exp_s_path['driver_volume_type'],
                         ret['driver_volume_type'])

        # Check the single path host-volume map return value
        for k, v in exp_s_path['data'].items():
            self.assertEqual(v, ret['data'][k])

        ret = self.iscsi_driver._helpers.get_host_from_connector(
            connector, iscsi=True)
        self.assertIsNotNone(ret)

    def test_storwize_initialize_iscsi_connection_multipath(self):
        # Test the return value for _get_iscsi_properties

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa',
                     'multipath': True}

        # Expected multipath host-volume map return value
        exp_m_path = {'driver_volume_type': 'iscsi',
                      'data': {'target_discovered': False,
                               'target_iqn':
                                   'iqn.1982-01.com.ibm:1234.sim.node1',
                               'target_portal': '1.234.56.78:3260',
                               'target_lun': 0,
                               'target_iqns': [
                                   'iqn.1982-01.com.ibm:1234.sim.node1',
                                   'iqn.1982-01.com.ibm:1234.sim.node1',
                                   'iqn.1982-01.com.ibm:1234.sim.node2'],
                               'target_portals':
                                   ['1.234.56.78:3260',
                                    '1.234.56.80:3260',
                                    '1.234.56.79:3260'],
                               'target_luns': [0, 0, 0],
                               'auth_method': 'CHAP',
                               'discovery_auth_method': 'CHAP'}}

        volume_iSCSI = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> iSCSI'}
        vol_type_iSCSI = volume_types.create(self.ctxt, 'iSCSI', extra_spec)
        volume_iSCSI['volume_type_id'] = vol_type_iSCSI['id']

        # Check case where no hosts exist
        ret = self.iscsi_driver._helpers.get_host_from_connector(
            connector, iscsi=True)
        self.assertIsNone(ret)

        # Initialize connection to map volume to a host
        ret = self.iscsi_driver.initialize_connection(
            volume_iSCSI, connector)
        self.assertEqual(exp_m_path['driver_volume_type'],
                         ret['driver_volume_type'])

        # Check the multipath host-volume map return value
        for k, v in exp_m_path['data'].items():
            self.assertEqual(v, ret['data'][k])

        ret = self.iscsi_driver._helpers.get_host_from_connector(
            connector, iscsi=True)
        self.assertIsNotNone(ret)

    def test_storwize_svc_iscsi_host_maps(self):
        # Create two volumes to be used in mappings

        ctxt = context.get_admin_context()
        volume1 = self._generate_vol_info(None, None)
        self.iscsi_driver.create_volume(volume1)
        volume2 = self._generate_vol_info(None, None)
        self.iscsi_driver.create_volume(volume2)

        # Create volume types that we created
        types = {}
        for protocol in ['iSCSI']:
            opts = {'storage_protocol': '<in> ' + protocol}
            types[protocol] = volume_types.create(ctxt, protocol, opts)

        expected = {'iSCSI': {'driver_volume_type': 'iscsi',
                              'data': {'target_discovered': False,
                                       'target_iqn':
                                       'iqn.1982-01.com.ibm:1234.sim.node1',
                                       'target_portal': '1.234.56.78:3260',
                                       'target_lun': 0,
                                       'auth_method': 'CHAP',
                                       'discovery_auth_method': 'CHAP'}}}

        volume1['volume_type_id'] = types[protocol]['id']
        volume2['volume_type_id'] = types[protocol]['id']

        # Check case where no hosts exist
        if self.USESIM:
            ret = self.iscsi_driver._helpers.get_host_from_connector(
                self._connector)
            self.assertIsNone(ret)

        # Make sure that the volumes have been created
        self._assert_vol_exists(volume1['name'], True)
        self._assert_vol_exists(volume2['name'], True)

        # Initialize connection from the first volume to a host
        ret = self.iscsi_driver.initialize_connection(
            volume1, self._connector)
        self.assertEqual(expected[protocol]['driver_volume_type'],
                         ret['driver_volume_type'])
        for k, v in expected[protocol]['data'].items():
            self.assertEqual(v, ret['data'][k])

        # Initialize again, should notice it and do nothing
        ret = self.iscsi_driver.initialize_connection(
            volume1, self._connector)
        self.assertEqual(expected[protocol]['driver_volume_type'],
                         ret['driver_volume_type'])
        for k, v in expected[protocol]['data'].items():
            self.assertEqual(v, ret['data'][k])

        # Try to delete the 1st volume (should fail because it is mapped)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.iscsi_driver.delete_volume,
                          volume1)

        ret = self.iscsi_driver.terminate_connection(volume1,
                                                     self._connector)
        if self.USESIM:
            ret = self.iscsi_driver._helpers.get_host_from_connector(
                self._connector)
            self.assertIsNone(ret)

        # Check cases with no auth set for host
        if self.USESIM:
            for auth_enabled in [True, False]:
                for host_exists in ['yes-auth', 'yes-noauth', 'no']:
                    self._set_flag('storwize_svc_iscsi_chap_enabled',
                                   auth_enabled)
                    case = 'en' + six.text_type(
                        auth_enabled) + 'ex' + six.text_type(host_exists)
                    conn_na = {'initiator': 'test:init:%s' %
                                            random.randint(10000, 99999),
                               'ip': '11.11.11.11',
                               'host': 'host-%s' % case}
                    if host_exists.startswith('yes'):
                        self.sim._add_host_to_list(conn_na)
                        if host_exists == 'yes-auth':
                            kwargs = {'chapsecret': 'foo',
                                      'obj': conn_na['host']}
                            self.sim._cmd_chhost(**kwargs)
                    volume1['volume_type_id'] = types['iSCSI']['id']

                    init_ret = self.iscsi_driver.initialize_connection(volume1,
                                                                       conn_na)
                    host_name = self.sim._host_in_list(conn_na['host'])
                    chap_ret = (
                        self.iscsi_driver._helpers.get_chap_secret_for_host(
                            host_name))
                    if auth_enabled or host_exists == 'yes-auth':
                        self.assertIn('auth_password', init_ret['data'])
                        self.assertIsNotNone(chap_ret)
                    else:
                        self.assertNotIn('auth_password', init_ret['data'])
                        self.assertIsNone(chap_ret)
                    self.iscsi_driver.terminate_connection(volume1, conn_na)
        self._set_flag('storwize_svc_iscsi_chap_enabled', True)

        # Test no preferred node
        if self.USESIM:
            self.sim.error_injection('lsvdisk', 'no_pref_node')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.iscsi_driver.initialize_connection,
                              volume1, self._connector)

        # Initialize connection from the second volume to the host with no
        # preferred node set if in simulation mode, otherwise, just
        # another initialize connection.
        if self.USESIM:
            self.sim.error_injection('lsvdisk', 'blank_pref_node')
        self.iscsi_driver.initialize_connection(volume2, self._connector)

        # Try to remove connection from host that doesn't exist (should fail)
        conn_no_exist = self._connector.copy()
        conn_no_exist['initiator'] = 'i_dont_exist'
        conn_no_exist['wwpns'] = ['0000000000000000']
        self.assertRaises(exception.VolumeDriverException,
                          self.iscsi_driver.terminate_connection,
                          volume1,
                          conn_no_exist)

        # Try to remove connection from volume that isn't mapped (should print
        # message but NOT fail)
        unmapped_vol = self._generate_vol_info(None, None)
        self.iscsi_driver.create_volume(unmapped_vol)
        self.iscsi_driver.terminate_connection(unmapped_vol, self._connector)
        self.iscsi_driver.delete_volume(unmapped_vol)

        # Remove the mapping from the 1st volume and delete it
        self.iscsi_driver.terminate_connection(volume1, self._connector)
        self.iscsi_driver.delete_volume(volume1)
        self._assert_vol_exists(volume1['name'], False)

        # Make sure our host still exists
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            self._connector, iscsi=True)
        self.assertIsNotNone(host_name)

        # Remove the mapping from the 2nd volume. The host should
        # be automatically removed because there are no more mappings.
        self.iscsi_driver.terminate_connection(volume2, self._connector)

        # Check if we successfully terminate connections when the host is not
        # specified (see bug #1244257)
        fake_conn = {'ip': '127.0.0.1', 'initiator': 'iqn.fake'}
        self.iscsi_driver.initialize_connection(volume2, self._connector)
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            self._connector, iscsi=True)
        self.assertIsNotNone(host_name)
        self.iscsi_driver.terminate_connection(volume2, fake_conn)
        host_name = self.iscsi_driver._helpers.get_host_from_connector(
            self._connector, iscsi=True)
        self.assertIsNone(host_name)
        self.iscsi_driver.delete_volume(volume2)
        self._assert_vol_exists(volume2['name'], False)

        # Delete volume types that we created
        for protocol in ['iSCSI']:
            volume_types.destroy(ctxt, types[protocol]['id'])

        # Check if our host still exists (it should not)
        if self.USESIM:
            ret = (
                self.iscsi_driver._helpers.get_host_from_connector(
                    self._connector, iscsi=True))
            self.assertIsNone(ret)

    def test_storwize_svc_iscsi_multi_host_maps(self):
        # We can't test connecting to multiple hosts from a single host when
        # using real storage
        if not self.USESIM:
            return

        # Create a volume to be used in mappings
        ctxt = context.get_admin_context()
        volume = self._generate_vol_info(None, None)
        self.iscsi_driver.create_volume(volume)

        # Create volume types for protocols
        types = {}
        for protocol in ['iSCSI']:
            opts = {'storage_protocol': '<in> ' + protocol}
            types[protocol] = volume_types.create(ctxt, protocol, opts)

        # Create a connector for the second 'host'
        wwpns = [six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                 six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
        initiator = 'test.initiator.%s' % six.text_type(random.randint(10000,
                                                                       99999))
        conn2 = {'ip': '1.234.56.79',
                 'host': 'storwize-svc-test2',
                 'wwpns': wwpns,
                 'initiator': initiator}

        # Check protocols for iSCSI
        volume['volume_type_id'] = types[protocol]['id']

        # Make sure that the volume has been created
        self._assert_vol_exists(volume['name'], True)

        self.iscsi_driver.initialize_connection(volume, self._connector)

        self._set_flag('storwize_svc_multihostmap_enabled', False)
        self.assertRaises(
            exception.CinderException,
            self.iscsi_driver.initialize_connection, volume, conn2)

        self._set_flag('storwize_svc_multihostmap_enabled', True)
        self.iscsi_driver.initialize_connection(volume, conn2)

        self.iscsi_driver.terminate_connection(volume, conn2)
        self.iscsi_driver.terminate_connection(volume, self._connector)

    def test_add_vdisk_copy_iscsi(self):
        # Ensure only iSCSI is available
        self.iscsi_driver._state['enabled_protocols'] = set(['iSCSI'])
        volume = self._generate_vol_info(None, None)
        self.iscsi_driver.create_volume(volume)
        self.iscsi_driver.add_vdisk_copy(volume['name'], 'fake-pool', None)


class StorwizeSVCFcDriverTestCase(test.TestCase):
    @mock.patch.object(time, 'sleep')
    def setUp(self, mock_sleep):
        super(StorwizeSVCFcDriverTestCase, self).setUp()
        self.USESIM = True
        if self.USESIM:
            self.fc_driver = StorwizeSVCFcFakeDriver(
                configuration=conf.Configuration(None))
            self._def_flags = {'san_ip': 'hostname',
                               'san_login': 'user',
                               'san_password': 'pass',
                               'storwize_svc_volpool_name':
                               SVC_POOLS,
                               'storwize_svc_flashcopy_timeout': 20,
                               'storwize_svc_flashcopy_rate': 49,
                               'storwize_svc_multipath_enabled': False,
                               'storwize_svc_allow_tenant_qos': True}
            wwpns = [
                six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
            initiator = 'test.initiator.%s' % six.text_type(
                random.randint(10000, 99999))
            self._connector = {'ip': '1.234.56.78',
                               'host': 'storwize-svc-test',
                               'wwpns': wwpns,
                               'initiator': initiator}
            self.sim = StorwizeSVCManagementSimulator(SVC_POOLS)

            self.fc_driver.set_fake_storage(self.sim)
            self.ctxt = context.get_admin_context()

        self._reset_flags()
        self.ctxt = context.get_admin_context()
        db_driver = self.fc_driver.configuration.db_driver
        self.db = importutils.import_module(db_driver)
        self.fc_driver.db = self.db
        self.fc_driver.do_setup(None)
        self.fc_driver.check_for_setup_error()
        self.fc_driver._helpers.check_fcmapping_interval = 0

    def _set_flag(self, flag, value):
        group = self.fc_driver.configuration.config_group
        self.fc_driver.configuration.set_override(flag, value, group)

    def _reset_flags(self):
        self.fc_driver.configuration.local_conf.reset()
        for k, v in self._def_flags.items():
            self._set_flag(k, v)

    def _create_volume(self, **kwargs):
        pool = _get_test_pool()
        prop = {'host': 'openstack@svc#%s' % pool,
                'size': 1}
        for p in prop.keys():
            if p not in kwargs:
                kwargs[p] = prop[p]
        vol = testutils.create_volume(self.ctxt, **kwargs)
        self.fc_driver.create_volume(vol)
        return vol

    def _delete_volume(self, volume):
        self.fc_driver.delete_volume(volume)
        self.db.volume_destroy(self.ctxt, volume['id'])

    def _generate_vol_info(self, vol_name, vol_id):
        pool = _get_test_pool()
        prop = {'mdisk_grp_name': pool}
        if vol_name:
            prop.update(volume_name=vol_name,
                        volume_id=vol_id,
                        volume_size=10)
        else:
            prop.update(size=10,
                        volume_type_id=None,
                        mdisk_grp_name=pool,
                        host='openstack@svc#%s' % pool)
        vol = testutils.create_volume(self.ctxt, **prop)
        return vol

    def _assert_vol_exists(self, name, exists):
        is_vol_defined = self.fc_driver._helpers.is_vdisk_defined(name)
        self.assertEqual(exists, is_vol_defined)

    def test_storwize_get_host_with_fc_connection(self):
        # Create a FC host
        del self._connector['initiator']
        helper = self.fc_driver._helpers
        host_name = helper.create_host(self._connector)

        # Remove the first wwpn from connector, and then try get host
        wwpns = self._connector['wwpns']
        wwpns.remove(wwpns[0])
        host_name = helper.get_host_from_connector(self._connector)

        self.assertIsNotNone(host_name)

    def test_storwize_get_host_with_fc_connection_with_volume(self):
        # create a FC volume
        volume_fc = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(volume_fc)
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type_fc = volume_types.create(self.ctxt, 'FC', extra_spec)
        volume_fc['volume_type_id'] = vol_type_fc['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}
        self.fc_driver.initialize_connection(volume_fc, connector)
        # Create a FC host
        helper = self.fc_driver._helpers

        host_name = helper.get_host_from_connector(
            connector, volume_fc['name'])
        self.assertIsNotNone(host_name)

    def test_storwize_get_host_from_connector_with_lshost_failure(self):
        self.skipTest('Bug 1640205')
        self._connector.pop('initiator')
        helper = self.fc_driver._helpers
        # Create two hosts. The first is not related to the connector and
        # we use the simulator for that. The second is for the connector.
        # We will force the missing_host error for the first host, but
        # then tolerate and find the second host on the slow path normally.
        if self.USESIM:
            self.sim._cmd_mkhost(name='storwize-svc-test-9', hbawwpn='123456')
        helper.create_host(self._connector)
        # tell lshost to fail while calling get_host_from_connector
        if self.USESIM:
            # tell lshost to fail while called from get_host_from_connector
            self.sim.error_injection('lshost', 'missing_host')
            # tell lsfabric to skip rows so that we skip past fast path
            self.sim.error_injection('lsfabric', 'remove_rows')
        # Run test
        host_name = helper.get_host_from_connector(self._connector)

        self.assertIsNotNone(host_name)
        # Need to assert that lshost was actually called. The way
        # we do that is check that the next simulator error for lshost
        # has been reset.
        self.assertEqual(self.sim._next_cmd_error['lshost'], '',
                         "lshost was not called in the simulator. The "
                         "queued error still remains.")

    def test_storwize_get_host_from_connector_with_lshost_failure2(self):
        self._connector.pop('initiator')
        self._connector['wwpns'] = []  # Clearing will skip over fast-path
        helper = self.fc_driver._helpers
        if self.USESIM:
            # Add a host to the simulator. We don't need it to match the
            # connector since we will force a bad failure for lshost.
            self.sim._cmd_mkhost(name='DifferentHost', hbawwpn='123456')
            # tell lshost to fail badly while called from
            # get_host_from_connector
            self.sim.error_injection('lshost', 'bigger_troubles')
            self.assertRaises(exception.VolumeBackendAPIException,
                              helper.get_host_from_connector,
                              self._connector)

    def test_storwize_get_host_from_connector_not_found(self):
        self._connector.pop('initiator')
        helper = self.fc_driver._helpers
        # Create some hosts. The first is not related to the connector and
        # we use the simulator for that. The second is for the connector.
        # We will force the missing_host error for the first host, but
        # then tolerate and find the second host on the slow path normally.
        if self.USESIM:
            self.sim._cmd_mkhost(name='storwize-svc-test-3', hbawwpn='1234567')
            self.sim._cmd_mkhost(name='storwize-svc-test-2', hbawwpn='2345678')
            self.sim._cmd_mkhost(name='storwize-svc-test-1', hbawwpn='3456789')
            self.sim._cmd_mkhost(name='A-Different-host', hbawwpn='9345678')
            self.sim._cmd_mkhost(name='B-Different-host', hbawwpn='8345678')
            self.sim._cmd_mkhost(name='C-Different-host', hbawwpn='7345678')
        # tell lshost to fail while calling get_host_from_connector
        if self.USESIM:
            # tell lsfabric to skip rows so that we skip past fast path
            self.sim.error_injection('lsfabric', 'remove_rows')
        # Run test
        host_name = helper.get_host_from_connector(self._connector)

        self.assertIsNone(host_name)

    def test_storwize_get_host_from_connector_fast_path(self):
        self._connector.pop('initiator')
        helper = self.fc_driver._helpers
        # Create two hosts. Our lshost will return the hosts in sorted
        # Order. The extra host will be returned before the target
        # host. If we get detailed lshost info on our host without
        # gettting detailed info on the other host we used the fast path
        if self.USESIM:
            self.sim._cmd_mkhost(name='A-DifferentHost', hbawwpn='123456')
        helper.create_host(self._connector)
        # tell lshost to fail while calling get_host_from_connector
        if self.USESIM:
            # tell lshost to fail while called from get_host_from_connector
            self.sim.error_injection('lshost', 'fail_fastpath')
            # tell lsfabric to skip rows so that we skip past fast path
            self.sim.error_injection('lsfabric', 'remove_rows')
        # Run test
        host_name = helper.get_host_from_connector(self._connector)

        self.assertIsNotNone(host_name)
        # Need to assert that lshost was actually called. The way
        # we do that is check that the next simulator error for lshost
        # has not been reset.
        self.assertEqual(self.sim._next_cmd_error['lshost'], 'fail_fastpath',
                         "lshost was not called in the simulator. The "
                         "queued error still remains.")

    def test_storwize_initiator_multiple_wwpns_connected(self):

        # Generate us a test volume
        volume = self._create_volume()

        # Fibre Channel volume type
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type = volume_types.create(self.ctxt, 'FC', extra_spec)

        volume['volume_type_id'] = vol_type['id']

        # Make sure that the volumes have been created
        self._assert_vol_exists(volume['name'], True)

        # Set up one WWPN that won't match and one that will.
        self.fc_driver._state['storage_nodes']['1']['WWPN'] = [
            '123456789ABCDEF0', 'AABBCCDDEEFF0010']

        wwpns = ['ff00000000000000', 'ff00000000000001']
        connector = {'host': 'storwize-svc-test', 'wwpns': wwpns}

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_conn_fc_wwpns') as get_mappings:
            mapped_wwpns = ['AABBCCDDEEFF0001', 'AABBCCDDEEFF0002',
                            'AABBCCDDEEFF0010', 'AABBCCDDEEFF0012']
            get_mappings.return_value = mapped_wwpns

            # Initialize the connection
            init_ret = self.fc_driver.initialize_connection(volume, connector)

            # Make sure we return all wwpns which where mapped as part of the
            # connection
            self.assertEqual(mapped_wwpns,
                             init_ret['data']['target_wwn'])

    def test_storwize_svc_fc_validate_connector(self):
        conn_neither = {'host': 'host'}
        conn_iscsi = {'host': 'host', 'initiator': 'foo'}
        conn_fc = {'host': 'host', 'wwpns': 'bar'}
        conn_both = {'host': 'host', 'initiator': 'foo', 'wwpns': 'bar'}

        self.fc_driver._state['enabled_protocols'] = set(['FC'])
        self.fc_driver.validate_connector(conn_fc)
        self.fc_driver.validate_connector(conn_both)
        self.assertRaises(exception.InvalidConnectorException,
                          self.fc_driver.validate_connector, conn_iscsi)
        self.assertRaises(exception.InvalidConnectorException,
                          self.fc_driver.validate_connector, conn_neither)

        self.fc_driver._state['enabled_protocols'] = set(['iSCSI', 'FC'])
        self.fc_driver.validate_connector(conn_fc)
        self.fc_driver.validate_connector(conn_both)
        self.assertRaises(exception.InvalidConnectorException,
                          self.fc_driver.validate_connector, conn_neither)

    def test_storwize_terminate_fc_connection(self):
        # create a FC volume
        volume_fc = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type_fc = volume_types.create(self.ctxt, 'FC', extra_spec)
        volume_fc['volume_type_id'] = vol_type_fc['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.fc_driver.initialize_connection(volume_fc, connector)
        self.fc_driver.terminate_connection(volume_fc, connector)

    @mock.patch.object(storwize_svc_fc.StorwizeSVCFCDriver,
                       '_do_terminate_connection')
    @mock.patch.object(storwize_svc_fc.StorwizeSVCFCDriver,
                       '_do_initialize_connection')
    def test_storwize_do_terminate_fc_connection(self, init_conn,
                                                 term_conn):
        # create a FC volume
        volume_fc = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type_fc = volume_types.create(self.ctxt, 'FC', extra_spec)
        volume_fc['volume_type_id'] = vol_type_fc['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.fc_driver.initialize_connection(volume_fc, connector)
        self.fc_driver.terminate_connection(volume_fc, connector)
        init_conn.assert_called_once_with(volume_fc, connector)
        term_conn.assert_called_once_with(volume_fc, connector)

    @mock.patch.object(storwize_svc_fc.StorwizeSVCFCDriver,
                       '_do_terminate_connection')
    def test_storwize_initialize_fc_connection_failure(self, term_conn):
        # create a FC volume
        volume_fc = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type_fc = volume_types.create(self.ctxt, 'FC', extra_spec)
        volume_fc['volume_type_id'] = vol_type_fc['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}

        self.fc_driver._state['storage_nodes'] = {}
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.fc_driver.initialize_connection,
                          volume_fc, connector)
        term_conn.assert_called_once_with(volume_fc, connector)

    def test_storwize_terminate_fc_connection_multi_attach(self):
        # create a FC volume
        volume_fc = self._create_volume()
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type_fc = volume_types.create(self.ctxt, 'FC', extra_spec)
        volume_fc['volume_type_id'] = vol_type_fc['id']

        connector = {'host': 'storwize-svc-host',
                     'wwnns': ['20000090fa17311e', '20000090fa17311f'],
                     'wwpns': ['ff00000000000000', 'ff00000000000001'],
                     'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1aaa'}
        connector2 = {'host': 'STORWIZE-SVC-HOST',
                      'wwnns': ['30000090fa17311e', '30000090fa17311f'],
                      'wwpns': ['ffff000000000000', 'ffff000000000001'],
                      'initiator': 'iqn.1993-08.org.debian:01:eac5ccc1bbb'}

        # map and unmap the volume to two hosts normal case
        self.fc_driver.initialize_connection(volume_fc, connector)
        self.fc_driver.initialize_connection(volume_fc, connector2)
        # validate that the host entries are created
        for conn in [connector, connector2]:
            host = self.fc_driver._helpers.get_host_from_connector(conn)
            self.assertIsNotNone(host)
        self.fc_driver.terminate_connection(volume_fc, connector)
        self.fc_driver.terminate_connection(volume_fc, connector2)
        # validate that the host entries are deleted
        for conn in [connector, connector2]:
            host = self.fc_driver._helpers.get_host_from_connector(conn)
            self.assertIsNone(host)
        # map and unmap the volume to two hosts with the mapping gone
        self.fc_driver.initialize_connection(volume_fc, connector)
        self.fc_driver.initialize_connection(volume_fc, connector2)
        # Test multiple attachments case
        host_name = self.fc_driver._helpers.get_host_from_connector(connector2)
        self.fc_driver._helpers.unmap_vol_from_host(
            volume_fc['name'], host_name)
        host_name = self.fc_driver._helpers.get_host_from_connector(connector2)
        self.assertIsNotNone(host_name)
        with mock.patch.object(storwize_svc_common.StorwizeSSH,
                               'rmvdiskhostmap') as rmmap:
            rmmap.side_effect = Exception('boom')
            self.fc_driver.terminate_connection(volume_fc, connector2)
        host_name = self.fc_driver._helpers.get_host_from_connector(connector2)
        self.assertIsNone(host_name)
        # Test single attachment case
        self.fc_driver._helpers.unmap_vol_from_host(
            volume_fc['name'], host_name)
        with mock.patch.object(storwize_svc_common.StorwizeSSH,
                               'rmvdiskhostmap') as rmmap:
            rmmap.side_effect = Exception('boom')
            self.fc_driver.terminate_connection(volume_fc, connector)
        # validate that the host entries are deleted
        for conn in [connector, connector2]:
            host = self.fc_driver._helpers.get_host_from_connector(conn)
        self.assertIsNone(host)

    def test_storwize_initiator_target_map(self):
        # Generate us a test volume
        volume = self._create_volume()

        # FIbre Channel volume type
        extra_spec = {'capabilities:storage_protocol': '<in> FC'}
        vol_type = volume_types.create(self.ctxt, 'FC', extra_spec)

        volume['volume_type_id'] = vol_type['id']

        # Make sure that the volumes have been created
        self._assert_vol_exists(volume['name'], True)

        wwpns = ['ff00000000000000', 'ff00000000000001']
        connector = {'host': 'storwize-svc-test', 'wwpns': wwpns}

        # Initialise the connection
        init_ret = self.fc_driver.initialize_connection(volume, connector)

        # Check that the initiator_target_map is as expected
        init_data = {'driver_volume_type': 'fibre_channel',
                     'data': {'initiator_target_map':
                              {'ff00000000000000': ['AABBCCDDEEFF0011'],
                               'ff00000000000001': ['AABBCCDDEEFF0011']},
                              'target_discovered': False,
                              'target_lun': 0,
                              'target_wwn': ['AABBCCDDEEFF0011'],
                              'volume_id': volume['id']
                              }
                     }

        self.assertEqual(init_data, init_ret)

        # Terminate connection
        term_ret = self.fc_driver.terminate_connection(volume, connector)

        # Check that the initiator_target_map is as expected
        term_data = {'driver_volume_type': 'fibre_channel',
                     'data': {'initiator_target_map':
                              {'ff00000000000000': ['5005076802432ADE',
                                                    '5005076802332ADE',
                                                    '5005076802532ADE',
                                                    '5005076802232ADE',
                                                    '5005076802132ADE',
                                                    '5005086802132ADE',
                                                    '5005086802332ADE',
                                                    '5005086802532ADE',
                                                    '5005086802232ADE',
                                                    '5005086802432ADE'],
                               'ff00000000000001': ['5005076802432ADE',
                                                    '5005076802332ADE',
                                                    '5005076802532ADE',
                                                    '5005076802232ADE',
                                                    '5005076802132ADE',
                                                    '5005086802132ADE',
                                                    '5005086802332ADE',
                                                    '5005086802532ADE',
                                                    '5005086802232ADE',
                                                    '5005086802432ADE']}
                              }
                     }

        self.assertItemsEqual(term_data, term_ret)

    def test_storwize_svc_fc_host_maps(self):
        # Create two volumes to be used in mappings

        ctxt = context.get_admin_context()
        volume1 = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(volume1)
        volume2 = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(volume2)

        # Create volume types that we created
        types = {}
        for protocol in ['FC']:
            opts = {'storage_protocol': '<in> ' + protocol}
            types[protocol] = volume_types.create(ctxt, protocol, opts)

        expected = {'FC': {'driver_volume_type': 'fibre_channel',
                           'data': {'target_lun': 0,
                                    'target_wwn': ['AABBCCDDEEFF0011'],
                                    'target_discovered': False}}}

        volume1['volume_type_id'] = types[protocol]['id']
        volume2['volume_type_id'] = types[protocol]['id']

        # Check case where no hosts exist
        if self.USESIM:
            ret = self.fc_driver._helpers.get_host_from_connector(
                self._connector)
            self.assertIsNone(ret)

        # Make sure that the volumes have been created
        self._assert_vol_exists(volume1['name'], True)
        self._assert_vol_exists(volume2['name'], True)

        # Initialize connection from the first volume to a host
        ret = self.fc_driver.initialize_connection(
            volume1, self._connector)
        self.assertEqual(expected[protocol]['driver_volume_type'],
                         ret['driver_volume_type'])
        for k, v in expected[protocol]['data'].items():
            self.assertEqual(v, ret['data'][k])

        # Initialize again, should notice it and do nothing
        ret = self.fc_driver.initialize_connection(
            volume1, self._connector)
        self.assertEqual(expected[protocol]['driver_volume_type'],
                         ret['driver_volume_type'])
        for k, v in expected[protocol]['data'].items():
            self.assertEqual(v, ret['data'][k])

        # Try to delete the 1st volume (should fail because it is mapped)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.fc_driver.delete_volume,
                          volume1)

        # Check bad output from lsfabric for the 2nd volume
        if protocol == 'FC' and self.USESIM:
            for error in ['remove_field', 'header_mismatch']:
                self.sim.error_injection('lsfabric', error)
                self.assertRaises(exception.VolumeBackendAPIException,
                                  self.fc_driver.initialize_connection,
                                  volume2, self._connector)

            with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                                   'get_conn_fc_wwpns') as conn_fc_wwpns:
                conn_fc_wwpns.return_value = []
                ret = self.fc_driver.initialize_connection(volume2,
                                                           self._connector)

        ret = self.fc_driver.terminate_connection(volume1, self._connector)
        if protocol == 'FC' and self.USESIM:
            # For the first volume detach, ret['data'] should be empty
            # only ret['driver_volume_type'] returned
            self.assertEqual({}, ret['data'])
            self.assertEqual('fibre_channel', ret['driver_volume_type'])
            ret = self.fc_driver.terminate_connection(volume2,
                                                      self._connector)
            self.assertEqual('fibre_channel', ret['driver_volume_type'])
            # wwpn is randomly created
            self.assertNotEqual({}, ret['data'])
        if self.USESIM:
            ret = self.fc_driver._helpers.get_host_from_connector(
                self._connector)
            self.assertIsNone(ret)

        # Test no preferred node
        if self.USESIM:
            self.sim.error_injection('lsvdisk', 'no_pref_node')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.fc_driver.initialize_connection,
                              volume1, self._connector)

        # Initialize connection from the second volume to the host with no
        # preferred node set if in simulation mode, otherwise, just
        # another initialize connection.
        if self.USESIM:
            self.sim.error_injection('lsvdisk', 'blank_pref_node')
        self.fc_driver.initialize_connection(volume2, self._connector)

        # Try to remove connection from host that doesn't exist (should fail)
        conn_no_exist = self._connector.copy()
        conn_no_exist['initiator'] = 'i_dont_exist'
        conn_no_exist['wwpns'] = ['0000000000000000']
        self.assertRaises(exception.VolumeDriverException,
                          self.fc_driver.terminate_connection,
                          volume1,
                          conn_no_exist)

        # Try to remove connection from volume that isn't mapped (should print
        # message but NOT fail)
        unmapped_vol = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(unmapped_vol)
        self.fc_driver.terminate_connection(unmapped_vol, self._connector)
        self.fc_driver.delete_volume(unmapped_vol)

        # Remove the mapping from the 1st volume and delete it
        self.fc_driver.terminate_connection(volume1, self._connector)
        self.fc_driver.delete_volume(volume1)
        self._assert_vol_exists(volume1['name'], False)

        # Make sure our host still exists
        host_name = self.fc_driver._helpers.get_host_from_connector(
            self._connector)
        self.assertIsNotNone(host_name)

        # Remove the mapping from the 2nd volume. The host should
        # be automatically removed because there are no more mappings.
        self.fc_driver.terminate_connection(volume2, self._connector)

        # Check if we successfully terminate connections when the host is not
        # specified (see bug #1244257)
        fake_conn = {'ip': '127.0.0.1', 'initiator': 'iqn.fake'}
        self.fc_driver.initialize_connection(volume2, self._connector)
        host_name = self.fc_driver._helpers.get_host_from_connector(
            self._connector)
        self.assertIsNotNone(host_name)
        self.fc_driver.terminate_connection(volume2, fake_conn)
        host_name = self.fc_driver._helpers.get_host_from_connector(
            self._connector)
        self.assertIsNone(host_name)
        self.fc_driver.delete_volume(volume2)
        self._assert_vol_exists(volume2['name'], False)

        # Delete volume types that we created
        for protocol in ['FC']:
            volume_types.destroy(ctxt, types[protocol]['id'])

        # Check if our host still exists (it should not)
        if self.USESIM:
            ret = (self.fc_driver._helpers.get_host_from_connector(
                self._connector))
            self.assertIsNone(ret)

    def test_storwize_svc_fc_multi_host_maps(self):
        # We can't test connecting to multiple hosts from a single host when
        # using real storage
        if not self.USESIM:
            return

        # Create a volume to be used in mappings
        ctxt = context.get_admin_context()
        volume = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(volume)

        # Create volume types for protocols
        types = {}
        for protocol in ['FC']:
            opts = {'storage_protocol': '<in> ' + protocol}
            types[protocol] = volume_types.create(ctxt, protocol, opts)

        # Create a connector for the second 'host'
        wwpns = [six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                 six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
        initiator = 'test.initiator.%s' % six.text_type(random.randint(10000,
                                                                       99999))
        conn2 = {'ip': '1.234.56.79',
                 'host': 'storwize-svc-test2',
                 'wwpns': wwpns,
                 'initiator': initiator}

        # Check protocols for FC

        volume['volume_type_id'] = types[protocol]['id']

        # Make sure that the volume has been created
        self._assert_vol_exists(volume['name'], True)

        self.fc_driver.initialize_connection(volume, self._connector)

        self._set_flag('storwize_svc_multihostmap_enabled', False)
        self.assertRaises(
            exception.CinderException,
            self.fc_driver.initialize_connection, volume, conn2)

        self._set_flag('storwize_svc_multihostmap_enabled', True)
        self.fc_driver.initialize_connection(volume, conn2)

        self.fc_driver.terminate_connection(volume, conn2)
        self.fc_driver.terminate_connection(volume, self._connector)

    def test_add_vdisk_copy_fc(self):
        # Ensure only FC is available
        self.fc_driver._state['enabled_protocols'] = set(['FC'])
        volume = self._generate_vol_info(None, None)
        self.fc_driver.create_volume(volume)
        self.fc_driver.add_vdisk_copy(volume['name'], 'fake-pool', None)


@ddt.ddt
class StorwizeSVCCommonDriverTestCase(test.TestCase):
    @mock.patch.object(time, 'sleep')
    def setUp(self, mock_sleep):
        super(StorwizeSVCCommonDriverTestCase, self).setUp()
        self.USESIM = True
        if self.USESIM:
            self._def_flags = {'san_ip': 'hostname',
                               'storwize_san_secondary_ip': 'secondaryname',
                               'san_login': 'user',
                               'san_password': 'pass',
                               'storwize_svc_volpool_name':
                               SVC_POOLS,
                               'storwize_svc_flashcopy_timeout': 20,
                               'storwize_svc_flashcopy_rate': 49,
                               'storwize_svc_allow_tenant_qos': True}
            config = conf.Configuration(None)
            # Override any configs that may get set in __init__
            self._reset_flags(config)
            self.driver = StorwizeSVCISCSIFakeDriver(
                configuration=config)
            self._driver = storwize_svc_iscsi.StorwizeSVCISCSIDriver(
                configuration=config)
            wwpns = [
                six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
            initiator = 'test.initiator.%s' % six.text_type(
                random.randint(10000, 99999))
            self._connector = {'ip': '1.234.56.78',
                               'host': 'storwize-svc-test',
                               'wwpns': wwpns,
                               'initiator': initiator}
            self.sim = StorwizeSVCManagementSimulator(SVC_POOLS)

            self.driver.set_fake_storage(self.sim)
            self.ctxt = context.get_admin_context()

        else:
            self._reset_flags()
        self.ctxt = context.get_admin_context()
        db_driver = self.driver.configuration.db_driver
        self.db = importutils.import_module(db_driver)
        self.driver.db = self.db
        self.driver.do_setup(None)
        self.driver.check_for_setup_error()
        self.driver._helpers.check_fcmapping_interval = 0
        self.mock_object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                         'DEFAULT_GR_SLEEP', 0)
        self._create_test_volume_types()

    def _set_flag(self, flag, value, configuration=None):
        if not configuration:
            configuration = self.driver.configuration
        group = configuration.config_group
        configuration.set_override(flag, value, group)

    def _reset_flags(self, configuration=None):
        if not configuration:
            configuration = self.driver.configuration
        configuration.local_conf.reset()
        for k, v in self._def_flags.items():
            self._set_flag(k, v, configuration)

    def _assert_vol_exists(self, name, exists):
        is_vol_defined = self.driver._helpers.is_vdisk_defined(name)
        self.assertEqual(exists, is_vol_defined)

    def _create_test_volume_types(self):
        spec = {'mirror_pool': 'openstack1'}
        self.mirror_vol_type = self._create_volume_type(spec, 'mirror_type')
        self.default_vol_type = self._create_volume_type(None, 'default_type')

    def test_storwize_svc_connectivity(self):
        # Make sure we detect if the pool doesn't exist
        no_exist_pool = 'i-dont-exist-%s' % random.randint(10000, 99999)
        self._set_flag('storwize_svc_volpool_name', no_exist_pool)
        self.assertRaises(exception.InvalidInput,
                          self.driver.do_setup, None)
        self._reset_flags()

        # Check the case where the user didn't configure IP addresses
        # as well as receiving unexpected results from the storage
        if self.USESIM:
            self.sim.error_injection('lsnodecanister', 'header_mismatch')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.do_setup, None)
            self.sim.error_injection('lsnodecanister', 'remove_field')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.do_setup, None)
            self.sim.error_injection('lsportip', 'header_mismatch')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.do_setup, None)
            self.sim.error_injection('lsportip', 'remove_field')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.do_setup, None)

        # Check with bad parameters
        self._set_flag('san_ip', '')
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        self._set_flag('san_password', None)
        self._set_flag('san_private_key', None)
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        self._set_flag('storwize_svc_vol_grainsize', 42)
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        self._set_flag('storwize_svc_vol_compression', True)
        self._set_flag('storwize_svc_vol_rsize', -1)
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        self._set_flag('storwize_svc_vol_rsize', 2)
        self._set_flag('storwize_svc_vol_nofmtdisk', True)
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        self._set_flag('storwize_svc_vol_iogrp', 5)
        self.assertRaises(exception.InvalidInput,
                          self.driver.check_for_setup_error)
        self._reset_flags()

        if self.USESIM:
            self.sim.error_injection('lslicense', 'no_compression')
            self.sim.error_injection('lsguicapabilities', 'no_compression')
            self._set_flag('storwize_svc_vol_compression', True)
            self.driver.do_setup(None)
            self.assertRaises(exception.InvalidInput,
                              self.driver.check_for_setup_error)
            self._reset_flags()

        # Finally, check with good parameters
        self.driver.do_setup(None)

    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_set_up_with_san_ip(self, mock_ssh_execute, mock_ssh_pool):
        ssh_cmd = ['svcinfo']
        self._driver._run_ssh(ssh_cmd)

        mock_ssh_pool.assert_called_once_with(
            self._driver.configuration.san_ip,
            self._driver.configuration.san_ssh_port,
            self._driver.configuration.ssh_conn_timeout,
            self._driver.configuration.san_login,
            password=self._driver.configuration.san_password,
            privatekey=self._driver.configuration.san_private_key,
            min_size=self._driver.configuration.ssh_min_pool_conn,
            max_size=self._driver.configuration.ssh_max_pool_conn)

    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_set_up_with_secondary_ip(self, mock_ssh_execute,
                                              mock_ssh_pool):
        mock_ssh_pool.side_effect = [paramiko.SSHException, mock.MagicMock()]
        ssh_cmd = ['svcinfo']
        self._driver._run_ssh(ssh_cmd)

        mock_ssh_pool.assert_called_with(
            self._driver.configuration.storwize_san_secondary_ip,
            self._driver.configuration.san_ssh_port,
            self._driver.configuration.ssh_conn_timeout,
            self._driver.configuration.san_login,
            password=self._driver.configuration.san_password,
            privatekey=self._driver.configuration.san_private_key,
            min_size=self._driver.configuration.ssh_min_pool_conn,
            max_size=self._driver.configuration.ssh_max_pool_conn)

    @mock.patch.object(random, 'randint', mock.Mock(return_value=0))
    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_fail_to_secondary_ip(self, mock_ssh_execute,
                                          mock_ssh_pool):
        mock_ssh_execute.side_effect = [processutils.ProcessExecutionError,
                                        mock.MagicMock()]
        ssh_cmd = ['svcinfo']
        self._driver._run_ssh(ssh_cmd)

        mock_ssh_pool.assert_called_with(
            self._driver.configuration.storwize_san_secondary_ip,
            self._driver.configuration.san_ssh_port,
            self._driver.configuration.ssh_conn_timeout,
            self._driver.configuration.san_login,
            password=self._driver.configuration.san_password,
            privatekey=self._driver.configuration.san_private_key,
            min_size=self._driver.configuration.ssh_min_pool_conn,
            max_size=self._driver.configuration.ssh_max_pool_conn)

    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_secondary_ip_ssh_fail_to_san_ip(self, mock_ssh_execute,
                                                 mock_ssh_pool):
        mock_ssh_pool.side_effect = [
            paramiko.SSHException,
            mock.MagicMock(
                ip = self._driver.configuration.storwize_san_secondary_ip),
            mock.MagicMock()]
        mock_ssh_execute.side_effect = [processutils.ProcessExecutionError,
                                        mock.MagicMock()]
        ssh_cmd = ['svcinfo']
        self._driver._run_ssh(ssh_cmd)

        mock_ssh_pool.assert_called_with(
            self._driver.configuration.san_ip,
            self._driver.configuration.san_ssh_port,
            self._driver.configuration.ssh_conn_timeout,
            self._driver.configuration.san_login,
            password=self._driver.configuration.san_password,
            privatekey=self._driver.configuration.san_private_key,
            min_size=self._driver.configuration.ssh_min_pool_conn,
            max_size=self._driver.configuration.ssh_max_pool_conn)

    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_both_ip_set_failure(self, mock_ssh_execute,
                                         mock_ssh_pool):
        mock_ssh_pool.side_effect = [
            paramiko.SSHException,
            mock.MagicMock(),
            mock.MagicMock()]
        mock_ssh_execute.side_effect = [processutils.ProcessExecutionError,
                                        processutils.ProcessExecutionError]
        ssh_cmd = ['svcinfo']
        self.assertRaises(processutils.ProcessExecutionError,
                          self._driver._run_ssh, ssh_cmd)

    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_second_ip_not_set_failure(self, mock_ssh_execute,
                                               mock_ssh_pool):
        mock_ssh_execute.side_effect = [processutils.ProcessExecutionError,
                                        mock.MagicMock()]
        self._set_flag('storwize_san_secondary_ip', None)
        ssh_cmd = ['svcinfo']
        self.assertRaises(processutils.ProcessExecutionError,
                          self._driver._run_ssh, ssh_cmd)

    @mock.patch.object(random, 'randint', mock.Mock(return_value=0))
    @mock.patch.object(ssh_utils, 'SSHPool')
    @mock.patch.object(processutils, 'ssh_execute')
    def test_run_ssh_consistent_active_ip(self, mock_ssh_execute,
                                          mock_ssh_pool):
        ssh_cmd = ['svcinfo']
        self._driver._run_ssh(ssh_cmd)
        self._driver._run_ssh(ssh_cmd)
        self._driver._run_ssh(ssh_cmd)
        self.assertEqual(self._driver.configuration.san_ip,
                         self._driver.active_ip)
        mock_ssh_execute.side_effect = [paramiko.SSHException,
                                        mock.MagicMock(), mock.MagicMock()]
        self._driver._run_ssh(ssh_cmd)
        self._driver._run_ssh(ssh_cmd)
        self.assertEqual(self._driver.configuration.storwize_san_secondary_ip,
                         self._driver.active_ip)

    def _create_volume_type(self, opts, type_name):
        type_ref = volume_types.create(self.ctxt, type_name, opts)
        vol_type = objects.VolumeType.get_by_id(self.ctxt, type_ref['id'])
        return vol_type

    def _generate_vol_info(self, vol_type=None, size=10):
        pool = _get_test_pool()
        prop = {'size': size,
                'host': 'openstack@svc#%s' % pool}
        if vol_type:
            prop['volume_type_id'] = vol_type.id
        vol = testutils.create_volume(self.ctxt, **prop)
        return vol

    def _generate_snap_info(self, vol_id, size=10):
        prop = {'volume_id': vol_id,
                'volume_size': size}
        snap = testutils.create_snapshot(self.ctxt, **prop)
        return snap

    def _create_volume(self, **kwargs):
        pool = _get_test_pool()
        prop = {'host': 'openstack@svc#%s' % pool,
                'size': 1}
        for p in prop.keys():
            if p not in kwargs:
                kwargs[p] = prop[p]
        vol = testutils.create_volume(self.ctxt, **kwargs)
        self.driver.create_volume(vol)
        return vol

    def _delete_volume(self, volume):
        self.driver.delete_volume(volume)
        self.db.volume_destroy(self.ctxt, volume['id'])

    def _create_group_in_db(self, **kwargs):
        cg = testutils.create_group(self.ctxt, **kwargs)
        return cg

    def _create_group(self, **kwargs):
        grp = self._create_group_in_db(**kwargs)

        model_update = self.driver.create_group(self.ctxt, grp)
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'],
                         "CG created failed")
        return grp

    def _create_group_snapshot_in_db(self, group_id, **kwargs):
        group_snapshot = testutils.create_group_snapshot(self.ctxt,
                                                         group_id=group_id,
                                                         **kwargs)
        snapshots = []
        volumes = self.db.volume_get_all_by_generic_group(
            self.ctxt.elevated(), group_id)

        if not volumes:
            msg = _("Group is empty. No cgsnapshot will be created.")
            raise exception.InvalidGroup(reason=msg)

        for volume in volumes:
            snapshots.append(testutils.create_snapshot(
                self.ctxt, volume['id'],
                group_snapshot.id,
                group_snapshot.name,
                group_snapshot.id,
                fields.SnapshotStatus.CREATING))
        return group_snapshot, snapshots

    def _create_group_snapshot(self, cg_id, **kwargs):
        group_snapshot, snapshots = self._create_group_snapshot_in_db(
            cg_id, **kwargs)

        model_update, snapshots_model = (
            self.driver.create_group_snapshot(self.ctxt, group_snapshot,
                                              snapshots))
        self.assertEqual(fields.GroupSnapshotStatus.AVAILABLE,
                         model_update['status'],
                         "CGSnapshot created failed")

        for snapshot in snapshots_model:
            self.assertEqual(fields.SnapshotStatus.AVAILABLE,
                             snapshot['status'])
        return group_snapshot, snapshots

    def _create_test_vol(self, opts):
        ctxt = testutils.get_test_admin_context()
        type_ref = volume_types.create(ctxt, 'testtype', opts)
        volume = self._generate_vol_info()
        volume.volume_type_id = type_ref['id']
        volume.volume_typ = objects.VolumeType.get_by_id(ctxt,
                                                         type_ref['id'])
        self.driver.create_volume(volume)

        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.driver.delete_volume(volume)
        volume_types.destroy(ctxt, type_ref['id'])
        return attrs

    def _get_default_opts(self):
        opt = {'rsize': 2,
               'warning': 0,
               'autoexpand': True,
               'grainsize': 256,
               'compression': False,
               'easytier': True,
               'iogrp': '0',
               'qos': None,
               'replication': False,
               'stretched_cluster': None,
               'nofmtdisk': False,
               'mirror_pool': None}
        return opt

    @mock.patch.object(storwize_svc_common.StorwizeHelpers, 'add_vdisk_qos')
    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_get_vdisk_params')
    def test_storwize_svc_create_volume_with_qos(self, get_vdisk_params,
                                                 add_vdisk_qos):
        vol = testutils.create_volume(self.ctxt)
        fake_opts = self._get_default_opts()
        # If the qos is empty, chvdisk should not be called
        # for create_volume.
        get_vdisk_params.return_value = fake_opts
        self.driver.create_volume(vol)
        self._assert_vol_exists(vol['name'], True)
        self.assertFalse(add_vdisk_qos.called)
        self.driver.delete_volume(vol)

        # If the qos is not empty, chvdisk should be called
        # for create_volume.
        fake_opts['qos'] = {'IOThrottling': 5000}
        get_vdisk_params.return_value = fake_opts
        self.driver.create_volume(vol)
        self._assert_vol_exists(vol['name'], True)
        add_vdisk_qos.assert_called_once_with(vol['name'], fake_opts['qos'])

        self.driver.delete_volume(vol)
        self._assert_vol_exists(vol['name'], False)

    def test_storwize_svc_snapshots(self):
        vol1 = self._create_volume()
        snap1 = self._generate_snap_info(vol1.id)

        # Test timeout and volume cleanup
        self._set_flag('storwize_svc_flashcopy_timeout', 1)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_snapshot, snap1)
        self._assert_vol_exists(snap1['name'], False)
        self._reset_flags()

        # Test prestartfcmap failing
        with mock.patch.object(
                storwize_svc_common.StorwizeSSH, 'prestartfcmap') as prestart:
            prestart.side_effect = exception.VolumeBackendAPIException
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_snapshot, snap1)

        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
            self.sim.error_injection('startfcmap', 'bad_id')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_snapshot, snap1)
            self._assert_vol_exists(snap1['name'], False)
            self.sim.error_injection('prestartfcmap', 'bad_id')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_snapshot, snap1)
            self._assert_vol_exists(snap1['name'], False)

        # Test successful snapshot
        self.driver.create_snapshot(snap1)
        self._assert_vol_exists(snap1['name'], True)

        # Try to create a snapshot from an non-existing volume - should fail
        vol2 = self._generate_vol_info()
        snap_novol = self._generate_snap_info(vol2.id)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_snapshot,
                          snap_novol)

        # We support deleting a volume that has snapshots, so delete the volume
        # first
        self.driver.delete_volume(vol1)
        self.driver.delete_snapshot(snap1)

    def test_storwize_svc_create_cloned_volume(self):
        vol1 = self._create_volume()
        vol2 = testutils.create_volume(self.ctxt)
        vol3 = testutils.create_volume(self.ctxt)

        # Try to clone where source size > target size
        vol1['size'] = vol2['size'] + 1
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_cloned_volume,
                          vol2, vol1)
        self._assert_vol_exists(vol2['name'], False)

        # Try to clone where source size = target size
        vol1['size'] = vol2['size']
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_cloned_volume(vol2, vol1)
        if self.USESIM:
            # validate copyrate was set on the flash copy
            for i, fcmap in self.sim._fcmappings_list.items():
                if fcmap['target'] == vol1['name']:
                    self.assertEqual('49', fcmap['copyrate'])
        self._assert_vol_exists(vol2['name'], True)

        # Try to clone where  source size < target size
        vol3['size'] = vol1['size'] + 1
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_cloned_volume(vol3, vol1)
        if self.USESIM:
            # Validate copyrate was set on the flash copy
            for i, fcmap in self.sim._fcmappings_list.items():
                if fcmap['target'] == vol1['name']:
                    self.assertEqual('49', fcmap['copyrate'])
        self._assert_vol_exists(vol3['name'], True)

        # Delete in the 'opposite' order to make sure it works
        self.driver.delete_volume(vol3)
        self._assert_vol_exists(vol3['name'], False)
        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2['name'], False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1['name'], False)

    def test_storwize_svc_create_volume_from_snapshot(self):
        vol1 = self._create_volume()
        snap1 = self._generate_snap_info(vol1.id)
        self.driver.create_snapshot(snap1)
        vol2 = self._generate_vol_info()
        vol3 = self._generate_vol_info()

        # Try to create a volume from a non-existing snapshot
        vol_novol = self._generate_vol_info()
        snap_novol = self._generate_snap_info(vol_novol.id)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.create_volume_from_snapshot,
                          vol_novol,
                          snap_novol)

        # Fail the snapshot
        with mock.patch.object(
                storwize_svc_common.StorwizeSSH, 'prestartfcmap') as prestart:
            prestart.side_effect = exception.VolumeBackendAPIException
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_volume_from_snapshot,
                              vol2, snap1)
            self._assert_vol_exists(vol2['name'], False)

        # Try to create where volume size < snapshot size
        snap1['volume_size'] += 1
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_volume_from_snapshot,
                          vol2, snap1)
        self._assert_vol_exists(vol2['name'], False)
        snap1['volume_size'] -= 1

        # Try to create where volume size > snapshot size
        vol2['size'] += 1
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_volume_from_snapshot(vol2, snap1)
        self._assert_vol_exists(vol2['name'], True)
        vol2['size'] -= 1

        # Try to create where volume size = snapshot size
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_volume_from_snapshot(vol3, snap1)
        self._assert_vol_exists(vol3['name'], True)

        # Delete in the 'opposite' order to make sure it works
        self.driver.delete_volume(vol3)
        self._assert_vol_exists(vol3['name'], False)
        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2['name'], False)
        self.driver.delete_snapshot(snap1)
        self._assert_vol_exists(snap1['name'], False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1['name'], False)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers, 'add_vdisk_qos')
    def test_storwize_svc_create_volfromsnap_clone_with_qos(self,
                                                            add_vdisk_qos):
        vol1 = self._create_volume()
        snap1 = self._generate_snap_info(vol1.id)
        self.driver.create_snapshot(snap1)
        vol2 = self._generate_vol_info()
        vol3 = self._generate_vol_info()
        fake_opts = self._get_default_opts()

        # Succeed
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')

        # If the qos is empty, chvdisk should not be called
        # for create_volume_from_snapshot.
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            get_vdisk_params.return_value = fake_opts
            self.driver.create_volume_from_snapshot(vol2, snap1)
            self._assert_vol_exists(vol2['name'], True)
            self.assertFalse(add_vdisk_qos.called)
            self.driver.delete_volume(vol2)

            # If the qos is not empty, chvdisk should be called
            # for create_volume_from_snapshot.
            fake_opts['qos'] = {'IOThrottling': 5000}
            get_vdisk_params.return_value = fake_opts
            self.driver.create_volume_from_snapshot(vol2, snap1)
            self._assert_vol_exists(vol2['name'], True)
            add_vdisk_qos.assert_called_once_with(vol2['name'],
                                                  fake_opts['qos'])

            if self.USESIM:
                self.sim.error_injection('lsfcmap', 'speed_up')

            # If the qos is empty, chvdisk should not be called
            # for create_volume_from_snapshot.
            add_vdisk_qos.reset_mock()
            fake_opts['qos'] = None
            get_vdisk_params.return_value = fake_opts
            self.driver.create_cloned_volume(vol3, vol2)
            self._assert_vol_exists(vol3['name'], True)
            self.assertFalse(add_vdisk_qos.called)
            self.driver.delete_volume(vol3)

            # If the qos is not empty, chvdisk should be called
            # for create_volume_from_snapshot.
            fake_opts['qos'] = {'IOThrottling': 5000}
            get_vdisk_params.return_value = fake_opts
            self.driver.create_cloned_volume(vol3, vol2)
            self._assert_vol_exists(vol3['name'], True)
            add_vdisk_qos.assert_called_once_with(vol3['name'],
                                                  fake_opts['qos'])

        # Delete in the 'opposite' order to make sure it works
        self.driver.delete_volume(vol3)
        self._assert_vol_exists(vol3['name'], False)
        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2['name'], False)
        self.driver.delete_snapshot(snap1)
        self._assert_vol_exists(snap1['name'], False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1['name'], False)

    def test_storwize_svc_delete_vol_with_fcmap(self):
        vol1 = self._create_volume()
        # create two snapshots
        snap1 = self._generate_snap_info(vol1.id)
        snap2 = self._generate_snap_info(vol1.id)
        self.driver.create_snapshot(snap1)
        self.driver.create_snapshot(snap2)
        vol2 = self._generate_vol_info()
        vol3 = self._generate_vol_info()

        # Create vol from the second snapshot
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_volume_from_snapshot(vol2, snap2)
        if self.USESIM:
            # validate copyrate was set on the flash copy
            for i, fcmap in self.sim._fcmappings_list.items():
                if fcmap['target'] == vol2['name']:
                    self.assertEqual('copying', fcmap['status'])
        self._assert_vol_exists(vol2['name'], True)

        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_cloned_volume(vol3, vol2)

        if self.USESIM:
            # validate copyrate was set on the flash copy
            for i, fcmap in self.sim._fcmappings_list.items():
                if fcmap['target'] == vol3['name']:
                    self.assertEqual('copying', fcmap['status'])
        self._assert_vol_exists(vol3['name'], True)

        # Delete in the 'opposite' order to make sure it works
        self.driver.delete_volume(vol3)
        self._assert_vol_exists(vol3['name'], False)
        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2['name'], False)
        self.driver.delete_snapshot(snap2)
        self._assert_vol_exists(snap2['name'], False)
        self.driver.delete_snapshot(snap1)
        self._assert_vol_exists(snap1['name'], False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1['name'], False)

    def test_storwize_svc_volumes(self):
        # Create a first volume
        volume = self._generate_vol_info()
        self.driver.create_volume(volume)

        self.driver.ensure_export(None, volume)

        # Do nothing
        self.driver.create_export(None, volume, {})
        self.driver.remove_export(None, volume)

        # Make sure volume attributes are as they should be
        attributes = self.driver._helpers.get_vdisk_attributes(volume['name'])
        attr_size = float(attributes['capacity']) / units.Gi  # bytes to GB
        self.assertEqual(attr_size, float(volume['size']))
        pool = _get_test_pool()
        self.assertEqual(attributes['mdisk_grp_name'], pool)

        # Try to create the volume again (should fail)
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          volume)

        # Try to delete a volume that doesn't exist (should not fail)
        vol_no_exist = self._generate_vol_info()
        self.driver.delete_volume(vol_no_exist)
        # Ensure export for volume that doesn't exist (should not fail)
        self.driver.ensure_export(None, vol_no_exist)

        # Delete the volume
        self.driver.delete_volume(volume)

    def test_storwize_svc_volume_name(self):
        volume = self._generate_vol_info()
        self.driver.create_volume(volume)
        self.driver.ensure_export(None, volume)

        # Ensure lsvdisk can find the volume by name
        attributes = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertIn('name', attributes)
        self.assertEqual(volume['name'], attributes['name'])
        self.driver.delete_volume(volume)

    def test_storwize_svc_volume_params(self):
        # Option test matrix
        # Option        Value   Covered by test #
        # rsize         -1      1
        # rsize         2       2,3
        # warning       0       2
        # warning       80      3
        # autoexpand    True    2
        # autoexpand    False   3
        # grainsize     32      2
        # grainsize     256     3
        # compression   True    4
        # compression   False   2,3
        # easytier      True    1,3
        # easytier      False   2
        # iogrp         0       1
        # iogrp         1       2
        # nofmtdisk     False   1
        # nofmtdisk     True    1

        opts_list = []
        chck_list = []
        opts_list.append({'rsize': -1, 'easytier': True, 'iogrp': '0'})
        chck_list.append({'free_capacity': '0', 'easy_tier': 'on',
                          'IO_group_id': '0'})

        opts_list.append({'rsize': -1, 'nofmtdisk': False})
        chck_list.append({'formatted': 'yes'})

        opts_list.append({'rsize': -1, 'nofmtdisk': True})
        chck_list.append({'formatted': 'no'})

        test_iogrp = '1' if self.USESIM else '0'
        opts_list.append({'rsize': 2, 'compression': False, 'warning': 0,
                          'autoexpand': True, 'grainsize': 32,
                          'easytier': False, 'iogrp': test_iogrp})
        chck_list.append({'-free_capacity': '0', 'compressed_copy': 'no',
                          'warning': '0', 'autoexpand': 'on',
                          'grainsize': '32', 'easy_tier': 'off',
                          'IO_group_id': (test_iogrp)})
        opts_list.append({'rsize': 2, 'compression': False, 'warning': 80,
                          'autoexpand': False, 'grainsize': 256,
                          'easytier': True})
        chck_list.append({'-free_capacity': '0', 'compressed_copy': 'no',
                          'warning': '80', 'autoexpand': 'off',
                          'grainsize': '256', 'easy_tier': 'on'})
        opts_list.append({'rsize': 2, 'compression': True})
        chck_list.append({'-free_capacity': '0',
                          'compressed_copy': 'yes'})

        for idx in range(len(opts_list)):
            attrs = self._create_test_vol(opts_list[idx])
            for k, v in chck_list[idx].items():
                try:
                    if k[0] == '-':
                        k = k[1:]
                        self.assertNotEqual(v, attrs[k])
                    else:
                        self.assertEqual(v, attrs[k])
                except processutils.ProcessExecutionError as e:
                    if 'CMMVC7050E' not in e.stderr:
                        raise

    def test_storwize_svc_unicode_host_and_volume_names(self):
        # We'll check with iSCSI only - nothing protocol-dependent here
        self.driver.do_setup(None)

        rand_id = random.randint(10000, 99999)
        volume1 = self._generate_vol_info()
        self.driver.create_volume(volume1)
        self._assert_vol_exists(volume1['name'], True)

        self.assertRaises(exception.VolumeDriverException,
                          self.driver._helpers.create_host,
                          {'host': 12345})

        # Add a host first to make life interesting (this host and
        # conn['host'] should be translated to the same prefix, and the
        # initiator should differentiate
        tmpconn1 = {'initiator': u'unicode:initiator1.%s' % rand_id,
                    'ip': '10.10.10.10',
                    'host': u'unicode.foo}.bar{.baz-%s' % rand_id}
        self.driver._helpers.create_host(tmpconn1, iscsi=True)

        # Add a host with a different prefix
        tmpconn2 = {'initiator': u'unicode:initiator2.%s' % rand_id,
                    'ip': '10.10.10.11',
                    'host': u'unicode.hello.world-%s' % rand_id}
        self.driver._helpers.create_host(tmpconn2, iscsi=True)

        conn = {'initiator': u'unicode:initiator3.%s' % rand_id,
                'ip': '10.10.10.12',
                'host': u'unicode.foo}.bar}.baz-%s' % rand_id}
        self.driver.initialize_connection(volume1, conn)
        host_name = self.driver._helpers.get_host_from_connector(
            conn, iscsi=True)
        self.assertIsNotNone(host_name)
        self.driver.terminate_connection(volume1, conn)
        host_name = self.driver._helpers.get_host_from_connector(conn)
        self.assertIsNone(host_name)
        self.driver.delete_volume(volume1)

        # Clean up temporary hosts
        for tmpconn in [tmpconn1, tmpconn2]:
            host_name = self.driver._helpers.get_host_from_connector(
                tmpconn, iscsi=True)
            self.assertIsNotNone(host_name)
            self.driver._helpers.delete_host(host_name)

    def test_storwize_svc_delete_volume_snapshots(self):
        # Create a volume with two snapshots
        master = self._create_volume()

        # Fail creating a snapshot - will force delete the snapshot
        if self.USESIM and False:
            snap = self._generate_snap_info(master.id)
            self.sim.error_injection('startfcmap', 'bad_id')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_snapshot, snap)
            self._assert_vol_exists(snap['name'], False)

        # Delete a snapshot
        snap = self._generate_snap_info(master.id)
        self.driver.create_snapshot(snap)
        self._assert_vol_exists(snap['name'], True)
        self.driver.delete_snapshot(snap)
        self._assert_vol_exists(snap['name'], False)

        # Delete a volume with snapshots (regular)
        snap = self._generate_snap_info(master.id)
        self.driver.create_snapshot(snap)
        self._assert_vol_exists(snap['name'], True)
        self.driver.delete_volume(master)
        self._assert_vol_exists(master['name'], False)

        # Fail create volume from snapshot - will force delete the volume
        if self.USESIM:
            volfs = self._generate_vol_info()
            self.sim.error_injection('startfcmap', 'bad_id')
            self.sim.error_injection('lsfcmap', 'speed_up')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_volume_from_snapshot,
                              volfs, snap)
            self._assert_vol_exists(volfs['name'], False)

        # Create volume from snapshot and delete it
        volfs = self._generate_vol_info()
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_volume_from_snapshot(volfs, snap)
        self._assert_vol_exists(volfs['name'], True)
        self.driver.delete_volume(volfs)
        self._assert_vol_exists(volfs['name'], False)

        # Create volume from snapshot and delete the snapshot
        volfs = self._generate_vol_info()
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_volume_from_snapshot(volfs, snap)
        self.driver.delete_snapshot(snap)
        self._assert_vol_exists(snap['name'], False)

        # Fail create clone - will force delete the target volume
        if self.USESIM:
            clone = self._generate_vol_info()
            self.sim.error_injection('startfcmap', 'bad_id')
            self.sim.error_injection('lsfcmap', 'speed_up')
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver.create_cloned_volume,
                              clone, volfs)
            self._assert_vol_exists(clone['name'], False)

        # Create the clone, delete the source and target
        clone = self._generate_vol_info()
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_cloned_volume(clone, volfs)
        self._assert_vol_exists(clone['name'], True)
        self.driver.delete_volume(volfs)
        self._assert_vol_exists(volfs['name'], False)
        self.driver.delete_volume(clone)
        self._assert_vol_exists(clone['name'], False)

    @ddt.data((True, None), (True, 5), (False, -1), (False, 100))
    @ddt.unpack
    def test_storwize_svc_get_volume_stats(
            self, is_thin_provisioning_enabled, rsize):
        self._set_flag('reserved_percentage', 25)
        self._set_flag('storwize_svc_multihostmap_enabled', True)
        self._set_flag('storwize_svc_vol_rsize', rsize)
        stats = self.driver.get_volume_stats()
        for each_pool in stats['pools']:
            self.assertIn(each_pool['pool_name'],
                          self._def_flags['storwize_svc_volpool_name'])
            self.assertTrue(each_pool['multiattach'])
            self.assertLessEqual(each_pool['free_capacity_gb'],
                                 each_pool['total_capacity_gb'])
            self.assertLessEqual(each_pool['allocated_capacity_gb'],
                                 each_pool['total_capacity_gb'])
            self.assertEqual(25, each_pool['reserved_percentage'])
            self.assertEqual(is_thin_provisioning_enabled,
                             each_pool['thin_provisioning_support'])
            self.assertEqual(not is_thin_provisioning_enabled,
                             each_pool['thick_provisioning_support'])
            self.assertTrue(each_pool['consistent_group_snapshot_enabled'])
        if self.USESIM:
            expected = 'storwize-svc-sim'
            self.assertEqual(expected, stats['volume_backend_name'])
            for each_pool in stats['pools']:
                self.assertIn(each_pool['pool_name'],
                              self._def_flags['storwize_svc_volpool_name'])
                self.assertAlmostEqual(3328.0, each_pool['total_capacity_gb'])
                self.assertAlmostEqual(3287.5, each_pool['free_capacity_gb'])
                self.assertAlmostEqual(25.0,
                                       each_pool['allocated_capacity_gb'])
                if is_thin_provisioning_enabled:
                    self.assertAlmostEqual(
                        1576.96, each_pool['provisioned_capacity_gb'])

    def test_get_pool(self):
        ctxt = testutils.get_test_admin_context()
        type_ref = volume_types.create(ctxt, 'testtype', None)
        volume = self._generate_vol_info()
        volume.volume_type_id = type_ref['id']
        volume.volume_type = objects.VolumeType.get_by_id(ctxt,
                                                          type_ref['id'])
        self.driver.create_volume(volume)
        vol = self.driver._helpers.get_vdisk_attributes(volume.name)
        self.assertEqual(vol['mdisk_grp_name'],
                         self.driver.get_pool(volume))

        self.driver.delete_volume(volume)
        volume_types.destroy(ctxt, type_ref['id'])

    def test_storwize_svc_extend_volume(self):
        volume = self._create_volume()
        self.driver.extend_volume(volume, '13')
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        vol_size = int(attrs['capacity']) / units.Gi

        self.assertAlmostEqual(vol_size, 13)

        snap = self._generate_snap_info(volume.id)
        self.driver.create_snapshot(snap)
        self._assert_vol_exists(snap['name'], True)
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.extend_volume, volume, '16')

        self.driver.delete_snapshot(snap)
        self.driver.delete_volume(volume)

    @mock.patch.object(storwize_rep.StorwizeSVCReplicationGlobalMirror,
                       'create_relationship')
    @mock.patch.object(storwize_rep.StorwizeSVCReplicationGlobalMirror,
                       'extend_target_volume')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def _storwize_svc_extend_volume_replication(self,
                                                get_relationship,
                                                delete_relationship,
                                                extend_target_volume,
                                                create_relationship):
        fake_target = mock.Mock()
        rep_type = 'global'
        self.driver.replications[rep_type] = (
            self.driver.replication_factory(rep_type, fake_target))
        volume = self._create_volume()
        volume['replication_status'] = 'enabled'
        fake_target_vol = 'vol-target-id'
        get_relationship.return_value = {'aux_vdisk_name': fake_target_vol}
        with mock.patch.object(
                self.driver,
                '_get_volume_replicated_type_mirror') as mirror_type:
            mirror_type.return_value = 'global'
            self.driver.extend_volume(volume, '13')
            attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
            vol_size = int(attrs['capacity']) / units.Gi
            self.assertAlmostEqual(vol_size, 13)
            delete_relationship.assert_called_once_with(volume['name'])
            extend_target_volume.assert_called_once_with(fake_target_vol,
                                                         12)
            create_relationship.assert_called_once_with(volume,
                                                        fake_target_vol)

        self.driver.delete_volume(volume)

    def _storwize_svc_extend_volume_replication_failover(self):
        volume = self._create_volume()
        volume['replication_status'] = 'failed-over'
        with mock.patch.object(
                self.driver,
                '_get_volume_replicated_type_mirror') as mirror_type:
            mirror_type.return_value = 'global'
            self.driver.extend_volume(volume, '13')
            attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
            vol_size = int(attrs['capacity']) / units.Gi
            self.assertAlmostEqual(vol_size, 13)

        self.driver.delete_volume(volume)

    def _check_loc_info(self, capabilities, expected):
        host = {'host': 'foo', 'capabilities': capabilities}
        vol = {'name': 'test', 'id': 1, 'size': 1}
        ctxt = context.get_admin_context()
        moved, model_update = self.driver.migrate_volume(ctxt, vol, host)
        self.assertEqual(expected['moved'], moved)
        self.assertEqual(expected['model_update'], model_update)

    def test_storwize_svc_migrate_bad_loc_info(self):
        self._check_loc_info({}, {'moved': False, 'model_update': None})
        cap = {'location_info': 'foo'}
        self._check_loc_info(cap, {'moved': False, 'model_update': None})
        cap = {'location_info': 'FooDriver:foo:bar'}
        self._check_loc_info(cap, {'moved': False, 'model_update': None})
        cap = {'location_info': 'StorwizeSVCDriver:foo:bar'}
        self._check_loc_info(cap, {'moved': False, 'model_update': None})

    def test_storwize_svc_volume_migrate(self):
        # Make sure we don't call migrate_volume_vdiskcopy
        self.driver.do_setup(None)
        loc = ('StorwizeSVCDriver:' + self.driver._state['system_id'] +
               ':openstack2')
        cap = {'location_info': loc, 'extent_size': '256'}
        host = {'host': 'openstack@svc#openstack2', 'capabilities': cap}
        ctxt = context.get_admin_context()
        volume = self._create_volume()
        volume['volume_type_id'] = None
        self.driver.migrate_volume(ctxt, volume, host)
        self._delete_volume(volume)

    def test_storwize_svc_get_vdisk_params(self):
        self.driver.do_setup(None)
        fake_qos = {'qos:IOThrottling': 5000}
        expected_qos = {'IOThrottling': 5000}
        fake_opts = self._get_default_opts()
        # The parameters retured should be the same to the default options,
        # if the QoS is empty.
        vol_type_empty_qos = self._create_volume_type_qos(True, None)
        type_id = vol_type_empty_qos['id']
        params = self.driver._get_vdisk_params(type_id,
                                               volume_type=vol_type_empty_qos,
                                               volume_metadata=None)
        self.assertEqual(fake_opts, params)
        volume_types.destroy(self.ctxt, type_id)

        # If the QoS is set via the qos association with the volume type,
        # qos value should be set in the retured parameters.
        vol_type_qos = self._create_volume_type_qos(False, fake_qos)
        type_id = vol_type_qos['id']
        # If type_id is not none and volume_type is none, it should work fine.
        params = self.driver._get_vdisk_params(type_id, volume_type=None,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If type_id is not none and volume_type is not none, it should
        # work fine.
        params = self.driver._get_vdisk_params(type_id,
                                               volume_type=vol_type_qos,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If type_id is none and volume_type is not none, it should work fine.
        params = self.driver._get_vdisk_params(None, volume_type=vol_type_qos,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If both type_id and volume_type are none, no qos will be returned
        # in the parameter.
        params = self.driver._get_vdisk_params(None, volume_type=None,
                                               volume_metadata=None)
        self.assertIsNone(params['qos'])
        qos_spec = volume_types.get_volume_type_qos_specs(type_id)
        volume_types.destroy(self.ctxt, type_id)
        qos_specs.delete(self.ctxt, qos_spec['qos_specs']['id'])

        # If the QoS is set via the extra specs in the volume type,
        # qos value should be set in the retured parameters.
        vol_type_qos = self._create_volume_type_qos(True, fake_qos)
        type_id = vol_type_qos['id']
        # If type_id is not none and volume_type is none, it should work fine.
        params = self.driver._get_vdisk_params(type_id, volume_type=None,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If type_id is not none and volume_type is not none,
        # it should work fine.
        params = self.driver._get_vdisk_params(type_id,
                                               volume_type=vol_type_qos,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If type_id is none and volume_type is not none,
        # it should work fine.
        params = self.driver._get_vdisk_params(None,
                                               volume_type=vol_type_qos,
                                               volume_metadata=None)
        self.assertEqual(expected_qos, params['qos'])
        # If both type_id and volume_type are none, no qos will be returned
        # in the parameter.
        params = self.driver._get_vdisk_params(None, volume_type=None,
                                               volume_metadata=None)
        self.assertIsNone(params['qos'])
        volume_types.destroy(self.ctxt, type_id)

        # If the QoS is set in the volume metadata,
        # qos value should be set in the retured parameters.
        metadata = [{'key': 'qos:IOThrottling', 'value': 4000}]
        expected_qos_metadata = {'IOThrottling': 4000}
        params = self.driver._get_vdisk_params(None, volume_type=None,
                                               volume_metadata=metadata)
        self.assertEqual(expected_qos_metadata, params['qos'])

        # If the QoS is set both in the metadata and the volume type, the one
        # in the volume type will take effect.
        vol_type_qos = self._create_volume_type_qos(True, fake_qos)
        type_id = vol_type_qos['id']
        params = self.driver._get_vdisk_params(type_id, volume_type=None,
                                               volume_metadata=metadata)
        self.assertEqual(expected_qos, params['qos'])
        volume_types.destroy(self.ctxt, type_id)

        # If the QoS is set both via the qos association and the
        # extra specs, the one from the qos association will take effect.
        fake_qos_associate = {'qos:IOThrottling': 6000}
        expected_qos_associate = {'IOThrottling': 6000}
        vol_type_qos = self._create_volume_type_qos_both(fake_qos,
                                                         fake_qos_associate)
        type_id = vol_type_qos['id']
        params = self.driver._get_vdisk_params(type_id, volume_type=None,
                                               volume_metadata=None)
        self.assertEqual(expected_qos_associate, params['qos'])
        qos_spec = volume_types.get_volume_type_qos_specs(type_id)
        volume_types.destroy(self.ctxt, type_id)
        qos_specs.delete(self.ctxt, qos_spec['qos_specs']['id'])

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'disable_vdisk_qos')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'update_vdisk_qos')
    def test_storwize_svc_retype_no_copy(self, update_vdisk_qos,
                                         disable_vdisk_qos):
        self.driver.do_setup(None)
        loc = ('StorwizeSVCDriver:' + self.driver._state['system_id'] +
               ':openstack')
        cap = {'location_info': loc, 'extent_size': '128'}
        self.driver._stats = {'location_info': loc}
        host = {'host': 'openstack@svc#openstack', 'capabilities': cap}
        ctxt = context.get_admin_context()

        key_specs_old = {'easytier': False, 'warning': 2, 'autoexpand': True}
        key_specs_new = {'easytier': True, 'warning': 5, 'autoexpand': False}
        old_type_ref = volume_types.create(ctxt, 'old', key_specs_old)
        new_type_ref = volume_types.create(ctxt, 'new', key_specs_new)

        diff, _equal = volume_types.volume_types_diff(ctxt, old_type_ref['id'],
                                                      new_type_ref['id'])

        old_type = objects.VolumeType.get_by_id(ctxt,
                                                old_type_ref['id'])
        volume = self._generate_vol_info(old_type)
        volume['host'] = host['host']
        new_type = objects.VolumeType.get_by_id(ctxt,
                                                new_type_ref['id'])

        self.driver.create_volume(volume)
        self.driver.retype(ctxt, volume, new_type, diff, host)
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertEqual('on', attrs['easy_tier'], 'Volume retype failed')
        self.assertEqual('5', attrs['warning'], 'Volume retype failed')
        self.assertEqual('off', attrs['autoexpand'], 'Volume retype failed')
        self.driver.delete_volume(volume)

        fake_opts = self._get_default_opts()
        fake_opts_old = self._get_default_opts()
        fake_opts_old['qos'] = {'IOThrottling': 4000}
        fake_opts_qos = self._get_default_opts()
        fake_opts_qos['qos'] = {'IOThrottling': 5000}
        self.driver.create_volume(volume)
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for both the source and target volumes,
            # add_vdisk_qos and disable_vdisk_qos will not be called for
            # retype.
            get_vdisk_params.side_effect = [fake_opts, fake_opts]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            self.assertFalse(update_vdisk_qos.called)
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is specified for both source and target volumes,
            # add_vdisk_qos will be called for retype, and disable_vdisk_qos
            # will not be called.
            get_vdisk_params.side_effect = [fake_opts_old, fake_opts_qos]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            update_vdisk_qos.assert_called_with(volume['name'],
                                                fake_opts_qos['qos'])
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for source and speficied for target volume,
            # add_vdisk_qos will be called for retype, and disable_vdisk_qos
            # will not be called.
            get_vdisk_params.side_effect = [fake_opts, fake_opts_qos]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            update_vdisk_qos.assert_called_with(volume['name'],
                                                fake_opts_qos['qos'])
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for target volume and specified for source
            # volume, add_vdisk_qos will not be called for retype, and
            # disable_vdisk_qos will be called.
            get_vdisk_params.side_effect = [fake_opts_qos, fake_opts]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            self.assertFalse(update_vdisk_qos.called)
            disable_vdisk_qos.assert_called_with(volume['name'],
                                                 fake_opts_qos['qos'])
            self.driver.delete_volume(volume)

    def test_storwize_svc_retype_only_change_iogrp(self):
        self.driver.do_setup(None)
        loc = ('StorwizeSVCDriver:' + self.driver._state['system_id'] +
               ':openstack')
        cap = {'location_info': loc, 'extent_size': '128'}
        self.driver._stats = {'location_info': loc}
        host = {'host': 'openstack@svc#openstack', 'capabilities': cap}
        ctxt = context.get_admin_context()

        key_specs_old = {'iogrp': 0}
        key_specs_new = {'iogrp': 1}
        old_type_ref = volume_types.create(ctxt, 'old', key_specs_old)
        new_type_ref = volume_types.create(ctxt, 'new', key_specs_new)

        diff, _equal = volume_types.volume_types_diff(ctxt, old_type_ref['id'],
                                                      new_type_ref['id'])

        old_type = objects.VolumeType.get_by_id(ctxt,
                                                old_type_ref['id'])
        volume = self._generate_vol_info(old_type)
        volume['host'] = host['host']
        new_type = objects.VolumeType.get_by_id(ctxt,
                                                new_type_ref['id'])

        self.driver.create_volume(volume)
        self.driver.retype(ctxt, volume, new_type, diff, host)
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertEqual('1', attrs['IO_group_id'], 'Volume retype '
                         'failed')
        self.driver.delete_volume(volume)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'disable_vdisk_qos')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'update_vdisk_qos')
    def test_storwize_svc_retype_need_copy(self, update_vdisk_qos,
                                           disable_vdisk_qos):
        self.driver.do_setup(None)
        loc = ('StorwizeSVCDriver:' + self.driver._state['system_id'] +
               ':openstack')
        cap = {'location_info': loc, 'extent_size': '128'}
        self.driver._stats = {'location_info': loc}
        host = {'host': 'openstack@svc#openstack', 'capabilities': cap}
        ctxt = context.get_admin_context()

        key_specs_old = {'compression': True, 'iogrp': 0}
        key_specs_new = {'compression': False, 'iogrp': 1}
        old_type_ref = volume_types.create(ctxt, 'old', key_specs_old)
        new_type_ref = volume_types.create(ctxt, 'new', key_specs_new)

        diff, _equal = volume_types.volume_types_diff(ctxt, old_type_ref['id'],
                                                      new_type_ref['id'])

        old_type = objects.VolumeType.get_by_id(ctxt,
                                                old_type_ref['id'])
        volume = self._generate_vol_info(old_type)
        volume['host'] = host['host']
        new_type = objects.VolumeType.get_by_id(ctxt,
                                                new_type_ref['id'])

        self.driver.create_volume(volume)
        self.driver.retype(ctxt, volume, new_type, diff, host)
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertEqual('no', attrs['compressed_copy'])
        self.assertEqual('1', attrs['IO_group_id'], 'Volume retype '
                         'failed')
        self.driver.delete_volume(volume)

        fake_opts = self._get_default_opts()
        fake_opts_old = self._get_default_opts()
        fake_opts_old['qos'] = {'IOThrottling': 4000}
        fake_opts_qos = self._get_default_opts()
        fake_opts_qos['qos'] = {'IOThrottling': 5000}
        self.driver.create_volume(volume)
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for both the source and target volumes,
            # add_vdisk_qos and disable_vdisk_qos will not be called for
            # retype.
            get_vdisk_params.side_effect = [fake_opts, fake_opts]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            self.assertFalse(update_vdisk_qos.called)
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is specified for both source and target volumes,
            # add_vdisk_qos will be called for retype, and disable_vdisk_qos
            # will not be called.
            get_vdisk_params.side_effect = [fake_opts_old, fake_opts_qos]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            update_vdisk_qos.assert_called_with(volume['name'],
                                                fake_opts_qos['qos'])
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for source and speficied for target volume,
            # add_vdisk_qos will be called for retype, and disable_vdisk_qos
            # will not be called.
            get_vdisk_params.side_effect = [fake_opts, fake_opts_qos]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            update_vdisk_qos.assert_called_with(volume['name'],
                                                fake_opts_qos['qos'])
            self.assertFalse(disable_vdisk_qos.called)
            self.driver.delete_volume(volume)

        self.driver.create_volume(volume)
        update_vdisk_qos.reset_mock()
        with mock.patch.object(storwize_svc_iscsi.StorwizeSVCISCSIDriver,
                               '_get_vdisk_params') as get_vdisk_params:
            # If qos is empty for target volume and specified for source
            # volume, add_vdisk_qos will not be called for retype, and
            # disable_vdisk_qos will be called.
            get_vdisk_params.side_effect = [fake_opts_qos, fake_opts]
            self.driver.retype(ctxt, volume, new_type, diff, host)
            self.assertFalse(update_vdisk_qos.called)
            disable_vdisk_qos.assert_called_with(volume['name'],
                                                 fake_opts_qos['qos'])
            self.driver.delete_volume(volume)

    def test_set_storage_code_level_success(self):
        res = self.driver._helpers.get_system_info()
        if self.USESIM:
            self.assertEqual((7, 2, 0, 0), res['code_level'],
                             'Get code level error')

    @mock.patch.object(storwize_svc_common.StorwizeHelpers, 'rename_vdisk')
    def test_storwize_update_migrated_volume(self, rename_vdisk):
        ctxt = testutils.get_test_admin_context()
        backend_volume = self._create_volume()
        volume = self._create_volume()
        model_update = self.driver.update_migrated_volume(ctxt, volume,
                                                          backend_volume,
                                                          'available')
        rename_vdisk.assert_called_once_with(backend_volume.name, volume.name)
        self.assertEqual({'_name_id': None}, model_update)

        rename_vdisk.reset_mock()
        rename_vdisk.side_effect = exception.VolumeBackendAPIException
        model_update = self.driver.update_migrated_volume(ctxt, volume,
                                                          backend_volume,
                                                          'available')
        self.assertEqual({'_name_id': backend_volume.id}, model_update)

        rename_vdisk.reset_mock()
        rename_vdisk.side_effect = exception.VolumeBackendAPIException
        model_update = self.driver.update_migrated_volume(ctxt, volume,
                                                          backend_volume,
                                                          'attached')
        self.assertEqual({'_name_id': backend_volume.id}, model_update)

    def test_storwize_vdisk_copy_ops(self):
        ctxt = testutils.get_test_admin_context()
        volume = self._create_volume()
        driver = self.driver
        dest_pool = volume_utils.extract_host(volume['host'], 'pool')
        new_ops = driver._helpers.add_vdisk_copy(volume['name'], dest_pool,
                                                 None, self.driver._state,
                                                 self.driver.configuration)
        self.driver._add_vdisk_copy_op(ctxt, volume, new_ops)
        admin_metadata = self.db.volume_admin_metadata_get(ctxt, volume['id'])
        self.assertEqual(":".join(x for x in new_ops),
                         admin_metadata['vdiskcopyops'],
                         'Storwize driver add vdisk copy error.')
        self.driver._check_volume_copy_ops()
        self.driver._rm_vdisk_copy_op(ctxt, volume, new_ops[0], new_ops[1])
        admin_metadata = self.db.volume_admin_metadata_get(ctxt, volume['id'])
        self.assertIsNone(admin_metadata.get('vdiskcopyops', None),
                          'Storwize driver delete vdisk copy error')
        self._delete_volume(volume)

    def test_storwize_delete_with_vdisk_copy_ops(self):
        volume = self._create_volume()
        self.driver._vdiskcopyops = {volume['id']: [('0', '1')]}
        with mock.patch.object(self.driver, '_vdiskcopyops_loop'):
            self.assertIn(volume['id'], self.driver._vdiskcopyops)
            self.driver.delete_volume(volume)
            self.assertNotIn(volume['id'], self.driver._vdiskcopyops)

    def test_storwize_create_volume_with_replication_disable(self):
        volume = self._generate_vol_info()

        model_update = self.driver.create_volume(volume)
        self.assertIsNone(model_update)

        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertIsNone(model_update)

    def test_storwize_create_volume_with_strech_cluster_replication(self):
        # Set replication flag, set pool openstack2 for secondary volume.
        self._set_flag('storwize_svc_stretched_cluster_partner', 'openstack2')

        # Create a type for repliation.
        volume = self._generate_vol_info()
        volume_type = self._create_replication_volume_type(True)
        volume['volume_type_id'] = volume_type['id']

        self.driver.do_setup(self.ctxt)

        model_update = self.driver.create_volume(volume)
        self.assertEqual('copying', model_update['replication_status'])

        volume['replication_status'] = 'copying'
        volume['replication_extended_status'] = None

        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('copying', model_update['replication_status'])

        # Primary copy offline, secondary copy online, data consistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'offline')
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('active-stop', model_update['replication_status'])

        # Primary copy offline, secondary copy online, data inconsistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'No',
                                       copy="secondary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('error', model_update['replication_status'])

        # Primary copy online, secondary copy offline, data consistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'yes',
                                       copy="secondary")
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'offline',
                                       copy="secondary")
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'online')
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('error', model_update['replication_status'])

        # Primary copy online, secondary copy offline, data inconsistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'no',
                                       copy="secondary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('error', model_update['replication_status'])

        # Primary copy offline, secondary copy offline, data consistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'yes',
                                       copy="secondary")
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'offline',
                                       copy="primary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('error', model_update['replication_status'])

        # Primary copy offline, secondary copy offline, data inconsistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'no',
                                       copy="secondary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('error', model_update['replication_status'])

        # Primary copy online, secondary copy online, data inconsistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'online',
                                       copy="secondary")
        self.sim.change_vdiskcopy_attr(volume['name'], 'status', 'online',
                                       copy="primary")
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'no',
                                       copy="secondary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('copying', model_update['replication_status'])

        # Primary copy online, secondary copy online, data consistent
        self.sim.change_vdiskcopy_attr(volume['name'], 'sync', 'yes',
                                       copy="secondary")
        model_update = self.driver.get_replication_status(self.ctxt, volume)
        self.assertEqual('active', model_update['replication_status'])

        # Check the volume copy created on pool openstack2.
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertIn('openstack2', attrs['mdisk_grp_name'])

        primary_status = attrs['primary']
        self.driver.promote_replica(self.ctxt, volume)

        # After promote_replica, primary copy should be swiched.
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertEqual(primary_status[0], attrs['primary'][1])
        self.assertEqual(primary_status[1], attrs['primary'][0])

        self.driver.delete_volume(volume)
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertIsNone(attrs)

    def test_storwize_create_cloned_volume_with_strech_cluster_replica(self):
        # Set replication flag, set pool openstack2 for secondary volume.
        self._set_flag('storwize_svc_stretched_cluster_partner', 'openstack2')
        self.driver.do_setup(self.ctxt)

        # Create a source volume.
        src_volume = self._generate_vol_info()
        self.driver.create_volume(src_volume)

        # Create a type for repliation.
        volume = self._generate_vol_info()
        volume_type = self._create_replication_volume_type(True)
        volume['volume_type_id'] = volume_type['id']

        # Create a cloned volume from source volume.
        model_update = self.driver.create_cloned_volume(volume, src_volume)
        self.assertEqual('copying', model_update['replication_status'])

        # Check the replication volume created on pool openstack2.
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        self.assertIn('openstack2', attrs['mdisk_grp_name'])

    def test_storwize_create_snapshot_volume_with_strech_cluster_replica(self):
        # Set replication flag, set pool openstack2 for secondary volume.
        self._set_flag('storwize_svc_stretched_cluster_partner', 'openstack2')
        self.driver.do_setup(self.ctxt)

        vol1 = self._create_volume()
        snap = self._generate_snap_info(vol1.id)
        self.driver.create_snapshot(snap)
        vol2 = self._generate_vol_info()

        # Create a type for repliation.
        vol2 = self._generate_vol_info()
        volume_type = self._create_replication_volume_type(True)
        vol2['volume_type_id'] = volume_type['id']

        model_update = self.driver.create_volume_from_snapshot(vol2, snap)
        self._assert_vol_exists(vol2['name'], True)
        self.assertEqual('copying', model_update['replication_status'])
        # Check the replication volume created on pool openstack2.
        attrs = self.driver._helpers.get_vdisk_attributes(vol2['name'])
        self.assertIn('openstack2', attrs['mdisk_grp_name'])

    # Test groups operation ####
    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_group_create_with_replication(
            self, is_grp_a_cg_snapshot_type):
        """Test group create."""
        is_grp_a_cg_snapshot_type.side_effect = True
        spec = {'replication_enabled': '<is> True',
                'replication_type': '<in> metro'}
        rep_type_ref = volume_types.create(self.ctxt, 'rep_type', spec)
        rep_group = testutils.create_group(
            self.ctxt, group_type_id=fake.GROUP_TYPE_ID,
            volume_type_ids=[rep_type_ref['id']])

        model_update = self.driver.create_group(self.ctxt, rep_group)
        self.assertEqual(fields.GroupStatus.ERROR,
                         model_update['status'])

        self.assertFalse(is_grp_a_cg_snapshot_type.called)

    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_group_create(self, is_grp_a_cg_snapshot_type):
        """Test group create."""
        is_grp_a_cg_snapshot_type.side_effect = [False, True]
        group = mock.MagicMock()

        self.assertRaises(NotImplementedError,
                          self.driver.create_group, self.ctxt, group)

        model_update = self.driver.create_group(self.ctxt, group)
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'])

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_delete_group(self, is_grp_a_cg_snapshot_type):
        is_grp_a_cg_snapshot_type.side_effect = [False, True]
        type_ref = volume_types.create(self.ctxt, 'testtype', None)
        group = testutils.create_group(self.ctxt,
                                       group_type_id=fake.GROUP_TYPE_ID,
                                       volume_type_id=type_ref['id'])

        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)
        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)
        volumes = self.db.volume_get_all_by_generic_group(
            self.ctxt.elevated(), group.id)
        self.assertRaises(NotImplementedError,
                          self.driver.delete_group,
                          self.ctxt, group, volumes)

        model_update = self.driver.delete_group(self.ctxt, group, volumes)
        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual('deleted', volume['status'])

    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_group_update(self, is_grp_a_cg_snapshot_type):
        """Test group update."""
        is_grp_a_cg_snapshot_type.side_effect = [False, True]
        group = mock.MagicMock()
        self.assertRaises(NotImplementedError, self.driver.update_group,
                          self.ctxt, group, None, None)

        (model_update, add_volumes_update,
         remove_volumes_update) = self.driver.update_group(self.ctxt, group)
        self.assertIsNone(model_update)
        self.assertIsNone(add_volumes_update)
        self.assertIsNone(remove_volumes_update)

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_create_group_snapshot(self, is_grp_a_cg_snapshot_type):
        is_grp_a_cg_snapshot_type.side_effect = [False, True]
        type_ref = volume_types.create(self.ctxt, 'testtype', None)
        group = testutils.create_group(self.ctxt,
                                       group_type_id=fake.GROUP_TYPE_ID,
                                       volume_type_id=type_ref['id'])

        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)
        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)
        group_snapshot, snapshots = self._create_group_snapshot_in_db(
            group.id)
        self.assertRaises(NotImplementedError,
                          self.driver.create_group_snapshot,
                          self.ctxt, group_snapshot, snapshots)

        (model_update,
         snapshots_model_update) = self.driver.create_group_snapshot(
            self.ctxt, group_snapshot, snapshots)
        self.assertEqual(fields.GroupSnapshotStatus.AVAILABLE,
                         model_update['status'],
                         "CGSnapshot created failed")

        for snapshot in snapshots_model_update:
            self.assertEqual(fields.SnapshotStatus.AVAILABLE,
                             snapshot['status'])

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    @mock.patch('cinder.volume.utils.is_group_a_cg_snapshot_type')
    def test_storwize_delete_group_snapshot(self, is_grp_a_cg_snapshot_type):
        is_grp_a_cg_snapshot_type.side_effect = [True, False, True]
        type_ref = volume_types.create(self.ctxt, 'testtype', None)
        group = testutils.create_group(self.ctxt,
                                       group_type_id=fake.GROUP_TYPE_ID,
                                       volume_type_id=type_ref['id'])

        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)
        self._create_volume(volume_type_id=type_ref['id'], group_id=group.id)

        group_snapshot, snapshots = self._create_group_snapshot(group.id)
        self.assertRaises(NotImplementedError,
                          self.driver.delete_group_snapshot,
                          self.ctxt, group_snapshot, snapshots)

        model_update = self.driver.delete_group_snapshot(self.ctxt,
                                                         group_snapshot,
                                                         snapshots)
        self.assertEqual(fields.GroupSnapshotStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual(fields.SnapshotStatus.DELETED, volume['status'])

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    def test_storwize_create_group_from_src_invalid(self):
        # Invalid input case for create group from src
        type_ref = volume_types.create(self.ctxt, 'testtype', None)
        spec = {'consistent_group_snapshot_enabled': '<is> True'}
        cg_type_ref = group_types.create(self.ctxt, 'cg_type', spec)
        vg_type_ref = group_types.create(self.ctxt, 'vg_type', None)

        # create group in db
        group = self._create_group_in_db(volume_type_id=type_ref.id,
                                         group_type_id=vg_type_ref.id)
        self.assertRaises(NotImplementedError,
                          self.driver.create_group_from_src,
                          self.ctxt, group, None, None, None,
                          None, None)

        group = self._create_group_in_db(volume_type_id=type_ref.id,
                                         group_type_id=cg_type_ref.id)

        # create volumes in db
        vol1 = testutils.create_volume(self.ctxt, volume_type_id=type_ref.id,
                                       group_id=group.id)
        vol2 = testutils.create_volume(self.ctxt, volume_type_id=type_ref.id,
                                       group_id=group.id)
        volumes = [vol1, vol2]

        source_cg = self._create_group_in_db(volume_type_id=type_ref.id,
                                             group_type_id=cg_type_ref.id)

        # Add volumes to source CG
        src_vol1 = self._create_volume(volume_type_id=type_ref.id,
                                       group_id=source_cg['id'])
        src_vol2 = self._create_volume(volume_type_id=type_ref.id,
                                       group_id=source_cg['id'])
        source_vols = [src_vol1, src_vol2]

        group_snapshot, snapshots = self._create_group_snapshot(
            source_cg['id'], group_type_id=cg_type_ref.id)

        # Create group from src with null input
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_group_from_src,
                          self.ctxt, group, volumes, None, None,
                          None, None)

        # Create cg from src with source_cg and empty source_vols
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_group_from_src,
                          self.ctxt, group, volumes, None, None,
                          source_cg, None)

        # Create cg from src with source_vols and empty source_cg
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_group_from_src,
                          self.ctxt, group, volumes, None, None,
                          None, source_vols)

        # Create cg from src with cgsnapshot and empty snapshots
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_group_from_src,
                          self.ctxt, group, volumes, group_snapshot, None,
                          None, None)
        # Create cg from src with snapshots and empty cgsnapshot
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_group_from_src,
                          self.ctxt, group, volumes, None, snapshots,
                          None, None)

        model_update = self.driver.delete_group(self.ctxt, group, volumes)

        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual('deleted', volume['status'])

        model_update = self.driver.delete_group(self.ctxt,
                                                source_cg, source_vols)

        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual('deleted', volume['status'])

        model_update = self.driver.delete_group(self.ctxt,
                                                group_snapshot, snapshots)

        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual('deleted', volume['status'])

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    def test_storwize_group_from_src(self):
        # Valid case for create cg from src
        type_ref = volume_types.create(self.ctxt, 'testtype', None)
        spec = {'consistent_group_snapshot_enabled': '<is> True'}
        cg_type_ref = group_types.create(self.ctxt, 'cg_type', spec)
        pool = _get_test_pool()
        # Create cg in db
        group = self._create_group_in_db(volume_type_id=type_ref.id,
                                         group_type_id=cg_type_ref.id)
        # Create volumes in db
        testutils.create_volume(self.ctxt, volume_type_id=type_ref.id,
                                group_id=group.id,
                                host='openstack@svc#%s' % pool)
        testutils.create_volume(self.ctxt, volume_type_id=type_ref.id,
                                consistencygroup_id=group.id,
                                host='openstack@svc#%s' % pool)
        volumes = self.db.volume_get_all_by_generic_group(
            self.ctxt.elevated(), group.id)

        # Create source CG
        source_cg = self._create_group_in_db(volume_type_id=type_ref.id,
                                             group_type_id=cg_type_ref.id)
        # Add volumes to source CG
        self._create_volume(volume_type_id=type_ref.id,
                            group_id=source_cg['id'])
        self._create_volume(volume_type_id=type_ref.id,
                            group_id=source_cg['id'])
        source_vols = self.db.volume_get_all_by_generic_group(
            self.ctxt.elevated(), source_cg['id'])

        # Create cgsnapshot
        group_snapshot, snapshots = self._create_group_snapshot(
            source_cg['id'], group_type_id=cg_type_ref.id)

        # Create cg from source cg
        model_update, volumes_model_update = (
            self.driver.create_group_from_src(self.ctxt, group, volumes, None,
                                              None, source_cg, source_vols))
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'],
                         "CG create from src created failed")
        for each_vol in volumes_model_update:
            self.assertEqual('available', each_vol['status'])

        model_update = self.driver.delete_group(self.ctxt, group, volumes)
        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for each_vol in model_update[1]:
            self.assertEqual('deleted', each_vol['status'])

        # Create cg from cg snapshot
        model_update, volumes_model_update = (
            self.driver.create_group_from_src(self.ctxt, group, volumes,
                                              group_snapshot, snapshots,
                                              None, None))
        self.assertEqual(fields.GroupStatus.AVAILABLE,
                         model_update['status'],
                         "CG create from src created failed")
        for each_vol in volumes_model_update:
            self.assertEqual('available', each_vol['status'])

        model_update = self.driver.delete_group(self.ctxt, group, volumes)
        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for each_vol in model_update[1]:
            self.assertEqual('deleted', each_vol['status'])

        model_update = self.driver.delete_group_snapshot(self.ctxt,
                                                         group_snapshot,
                                                         snapshots)
        self.assertEqual(fields.GroupStatus.DELETED,
                         model_update[0]['status'])
        for volume in model_update[1]:
            self.assertEqual('deleted', volume['status'])

    # mirror/strtch cluster volume test cases
    def test_storwize_svc_create_mirror_volume(self):
        # create mirror volume in invalid pool
        spec = {'mirror_pool': 'invalid_pool'}
        mirror_vol_type = self._create_volume_type(spec, 'invalid_mirror_type')
        vol = self._generate_vol_info(mirror_vol_type)
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_volume, vol)

        spec = {'mirror_pool': 'openstack1'}
        mirror_vol_type = self._create_volume_type(spec, 'test_mirror_type')
        vol = self._generate_vol_info(mirror_vol_type)
        self.driver.create_volume(vol)
        self._assert_vol_exists(vol.name, True)

        copies = self.driver._helpers.get_vdisk_copies(vol.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')
        self.driver.delete_volume(vol)
        self._assert_vol_exists(vol['name'], False)

    def test_storwize_svc_snapshots_mirror_volume(self):
        vol1 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume(vol1)

        snap1 = self._generate_snap_info(vol1.id)
        self._assert_vol_exists(snap1.name, False)

        self.driver.create_snapshot(snap1)
        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self._assert_vol_exists(snap1.name, True)
        copies = self.driver._helpers.get_vdisk_copies(snap1.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')

        self.driver.delete_snapshot(snap1)
        self.driver.delete_volume(vol1)

    def test_storwize_svc_create_cloned_mirror_volume(self):
        vol1 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume(vol1)
        vol2 = self._generate_vol_info(self.mirror_vol_type)

        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')
        self.driver.create_cloned_volume(vol2, vol1)
        self._assert_vol_exists(vol2.name, True)
        copies = self.driver._helpers.get_vdisk_copies(vol2.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')

        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2.name, False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1.name, False)

    def test_storwize_svc_create_mirror_volume_from_snapshot(self):
        vol1 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume(vol1)
        snap1 = self._generate_snap_info(vol1.id)
        self.driver.create_snapshot(snap1)

        if self.USESIM:
            self.sim.error_injection('lsfcmap', 'speed_up')

        vol2 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume_from_snapshot(vol2, snap1)
        self._assert_vol_exists(vol2.name, True)
        copies = self.driver._helpers.get_vdisk_copies(vol2.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')

        self.driver.delete_volume(vol2)
        self._assert_vol_exists(vol2['name'], False)
        self.driver.delete_snapshot(snap1)
        self._assert_vol_exists(snap1['name'], False)
        self.driver.delete_volume(vol1)
        self._assert_vol_exists(vol1['name'], False)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers, 'add_vdisk_copy')
    def test_storwize_svc_mirror_volume_migrate(self, add_vdisk_copy):
        # use migratevdisk for mirror volume migration, rather than
        # addvdiskcopy
        self.driver.do_setup(None)
        loc = ('StorwizeSVCDriver:' + self.driver._state['system_id'] +
               ':openstack2')
        host = {'host': 'openstack@svc#openstack2',
                'capabilities': {'location_info': loc}}
        ctxt = context.get_admin_context()
        vol1 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume(vol1)
        copies = self.driver._helpers.get_vdisk_copies(vol1.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')

        self.driver.migrate_volume(ctxt, vol1, host)
        copies = self.driver._helpers.get_vdisk_copies(vol1.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack2')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')
        self.assertFalse(add_vdisk_copy.called)
        self._delete_volume(vol1)

    @ddt.data(({'mirror_pool': 'openstack1'},
               {'mirror_pool': 'openstack1', 'compression': True}),
              ({'compression': False},
               {'mirror_pool': 'openstack1', 'compression': True}),
              ({}, {'mirror_pool': 'invalidpool'}))
    @ddt.unpack
    def test_storwize_svc_retype_mirror_volume_invalid(self, old_opts,
                                                       new_opts):
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}
        ctxt = context.get_admin_context()

        vol_type1 = self._create_volume_type(old_opts, 'old')
        vol_type2 = self._create_volume_type(new_opts, 'new')
        diff, _equal = volume_types.volume_types_diff(ctxt, vol_type1.id,
                                                      vol_type2.id)
        vol1 = self._generate_vol_info(vol_type1)
        self.driver.create_volume(vol1)

        self.assertRaises(exception.VolumeDriverException,
                          self.driver.retype, self.ctxt, vol1,
                          vol_type2, diff, host)
        self.driver.delete_volume(vol1)

    @ddt.data(({'mirror_pool': 'openstack1'}, {}),
              ({'mirror_pool': 'openstack1'}, {'mirror_pool': ''}))
    @ddt.unpack
    def test_storwize_retype_from_mirror_to_none_mirror(self,
                                                        old_opts, new_opts):
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}
        ctxt = context.get_admin_context()

        vol_type1 = self._create_volume_type(old_opts, 'old')
        vol_type2 = self._create_volume_type(new_opts, 'new')
        diff, _equal = volume_types.volume_types_diff(ctxt, vol_type1.id,
                                                      vol_type2.id)
        vol1 = self._generate_vol_info(vol_type1)
        self.driver.create_volume(vol1)

        self._assert_vol_exists(vol1.name, True)
        copies = self.driver._helpers.lsvdiskcopy(vol1.name)
        self.assertEqual(len(copies), 2)

        self.driver.retype(self.ctxt, vol1, vol_type2, diff, host)
        copies = self.driver._helpers.lsvdiskcopy(vol1.name)
        self.assertEqual(len(copies), 1)
        copies = self.driver._helpers.get_vdisk_copies(vol1.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')

        self.driver.delete_volume(vol1)

    @ddt.data(({}, {'mirror_pool': 'openstack1'}),
              ({'mirror_pool': ''}, {'mirror_pool': 'openstack1'}))
    @ddt.unpack
    def test_storwize_retype_from_none_to_mirror_volume(self,
                                                        old_opts, new_opts):
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}
        ctxt = context.get_admin_context()

        old_opts = {}
        new_opts = {'mirror_pool': 'openstack1'}
        vol_type1 = self._create_volume_type(old_opts, 'old')
        vol_type2 = self._create_volume_type(new_opts, 'new')
        diff, _equal = volume_types.volume_types_diff(ctxt, vol_type1.id,
                                                      vol_type2.id)
        vol1 = self._generate_vol_info(vol_type1)
        self.driver.create_volume(vol1)

        self._assert_vol_exists(vol1.name, True)
        copies = self.driver._helpers.lsvdiskcopy(vol1.name)
        self.assertEqual(len(copies), 1)

        self.driver.retype(self.ctxt, vol1, vol_type2, diff, host)
        copies = self.driver._helpers.lsvdiskcopy(vol1.name)
        self.assertEqual(len(copies), 2)
        copies = self.driver._helpers.get_vdisk_copies(vol1.name)
        self.assertEqual(copies['primary']['mdisk_grp_name'], 'openstack')
        self.assertEqual(copies['secondary']['mdisk_grp_name'], 'openstack1')

        self.driver.delete_volume(vol1)

    @ddt.data(({}, {'mirror_pool': 'openstack1'}),
              ({'mirror_pool': ''}, {'mirror_pool': 'openstack1'}),
              ({'mirror_pool': 'openstack1'}, {}),
              ({'mirror_pool': 'openstack1'}, {'mirror_pool': ''}),
              ({'mirror_pool': 'openstack1'}, {'mirror_pool': 'invalidpool'}))
    @ddt.unpack
    def test_storwize_manage_existing_mismatch_with_mirror_volume(
            self, opts1, opts2):
        self.driver.do_setup(self.ctxt)
        vol_type1 = self._create_volume_type(opts1, 'vol_type1')
        vol_type2 = self._create_volume_type(opts2, 'vol_type2')
        vol1 = self._generate_vol_info(vol_type1)
        self.driver.create_volume(vol1)
        vol2 = self._generate_vol_info(vol_type2)

        ref = {'source-name': vol1.name}
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, vol2, ref)

        self.driver.delete_volume(vol1)

    def test_storwize_manage_existing_with_mirror_volume(self):
        self.driver.do_setup(self.ctxt)
        vol1 = self._generate_vol_info(self.mirror_vol_type)
        self.driver.create_volume(vol1)
        uid_of_vol1 = self._get_vdisk_uid(vol1.name)

        opts1 = {'mirror_pool': 'openstack1'}
        new_volume_type = self._create_volume_type(opts1, 'new_mirror_type')
        new_volume = self._generate_vol_info(new_volume_type)
        ref = {'source-name': vol1.name}
        self.driver.manage_existing(new_volume, ref)

        # Check the uid of the volume which has been renamed.
        uid_of_new_vol = self._get_vdisk_uid(new_volume.name)
        self.assertEqual(uid_of_vol1, uid_of_new_vol)

        self.driver.delete_volume(new_volume)

    def _create_volume_type_qos(self, extra_specs, fake_qos):
        # Generate a QoS volume type for volume.
        if extra_specs:
            spec = fake_qos
            type_ref = volume_types.create(self.ctxt, "qos_extra_specs", spec)
        else:
            type_ref = volume_types.create(self.ctxt, "qos_associate", None)
            if fake_qos:
                qos_ref = qos_specs.create(self.ctxt, 'qos-specs', fake_qos)
                qos_specs.associate_qos_with_type(self.ctxt, qos_ref['id'],
                                                  type_ref['id'])

        qos_type = volume_types.get_volume_type(self.ctxt, type_ref['id'])
        return qos_type

    def _create_volume_type_qos_both(self, fake_qos, fake_qos_associate):
        type_ref = volume_types.create(self.ctxt, "qos_extra_specs", fake_qos)
        qos_ref = qos_specs.create(self.ctxt, 'qos-specs', fake_qos_associate)
        qos_specs.associate_qos_with_type(self.ctxt, qos_ref['id'],
                                          type_ref['id'])
        qos_type = volume_types.get_volume_type(self.ctxt, type_ref['id'])
        return qos_type

    def _create_replication_volume_type(self, enable):
        # Generate a volume type for volume repliation.
        if enable:
            spec = {'capabilities:replication': '<is> True'}
            type_ref = volume_types.create(self.ctxt, "replication_1", spec)
        else:
            spec = {'capabilities:replication': '<is> False'}
            type_ref = volume_types.create(self.ctxt, "replication_2", spec)

        replication_type = objects.VolumeType.get_by_id(self.ctxt,
                                                        type_ref['id'])
        return replication_type

    def _create_consistency_group_volume_type(self):
        # Generate a volume type for volume consistencygroup.
        spec = {'capabilities:consistencygroup_support': '<is> True'}
        type_ref = volume_types.create(self.ctxt, "cg", spec)

        cg_type = volume_types.get_volume_type(self.ctxt, type_ref['id'])

        return cg_type

    def _get_vdisk_uid(self, vdisk_name):
        """Return vdisk_UID for given vdisk.

        Given a vdisk by name, performs an lvdisk command that extracts
        the vdisk_UID parameter and returns it.
        Returns None if the specified vdisk does not exist.
        """
        vdisk_properties, _err = self.sim._cmd_lsvdisk(obj=vdisk_name,
                                                       delim='!')

        # Iterate through each row until we find the vdisk_UID entry
        for row in vdisk_properties.split('\n'):
            words = row.split('!')
            if words[0] == 'vdisk_UID':
                return words[1]
        return None

    def _create_volume_and_return_uid(self, volume_name):
        """Creates a volume and returns its UID.

        Creates a volume with the specified name, and returns the UID that
        the Storwize controller allocated for it.  We do this by executing a
        create_volume and then calling into the simulator to perform an
        lsvdisk directly.
        """
        volume = self._generate_vol_info()
        self.driver.create_volume(volume)

        return (volume, self._get_vdisk_uid(volume['name']))

    def test_manage_existing_get_size_bad_ref(self):
        """Error on manage with bad reference.

        This test case attempts to manage an existing volume but passes in
        a bad reference that the Storwize driver doesn't understand.  We
        expect an exception to be raised.
        """
        volume = self._generate_vol_info()
        ref = {}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing_get_size, volume, ref)

    def test_manage_existing_get_size_bad_uid(self):
        """Error when the specified UUID does not exist."""
        volume = self._generate_vol_info()
        ref = {'source-id': 'bad_uid'}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing_get_size, volume, ref)
        pass

    def test_manage_existing_get_size_bad_name(self):
        """Error when the specified name does not exist."""
        volume = self._generate_vol_info()
        ref = {'source-name': 'bad_name'}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing_get_size, volume, ref)

    def test_manage_existing_bad_ref(self):
        """Error on manage with bad reference.

        This test case attempts to manage an existing volume but passes in
        a bad reference that the Storwize driver doesn't understand.  We
        expect an exception to be raised.
        """

        # Error when neither UUID nor name are specified.
        volume = self._generate_vol_info()
        ref = {}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing, volume, ref)

        # Error when the specified UUID does not exist.
        volume = self._generate_vol_info()
        ref = {'source-id': 'bad_uid'}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing, volume, ref)

        # Error when the specified name does not exist.
        volume = self._generate_vol_info()
        ref = {'source-name': 'bad_name'}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing, volume, ref)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_vdisk_copy_attrs')
    def test_manage_existing_mismatch(self,
                                      get_vdisk_copy_attrs):
        ctxt = testutils.get_test_admin_context()
        _volume, uid = self._create_volume_and_return_uid('manage_test')

        opts = {'rsize': -1}
        type_thick_ref = volume_types.create(ctxt, 'testtype1', opts)

        opts = {'rsize': 2}
        type_thin_ref = volume_types.create(ctxt, 'testtype2', opts)

        opts = {'rsize': 2, 'compression': True}
        type_comp_ref = volume_types.create(ctxt, 'testtype3', opts)

        opts = {'rsize': -1, 'iogrp': 1}
        type_iogrp_ref = volume_types.create(ctxt, 'testtype4', opts)

        new_volume = self._generate_vol_info()
        ref = {'source-name': _volume['name']}

        fake_copy_thin = self._get_default_opts()
        fake_copy_thin['autoexpand'] = 'on'

        fake_copy_comp = self._get_default_opts()
        fake_copy_comp['autoexpand'] = 'on'
        fake_copy_comp['compressed_copy'] = 'yes'

        fake_copy_thick = self._get_default_opts()
        fake_copy_thick['autoexpand'] = ''
        fake_copy_thick['compressed_copy'] = 'no'

        fake_copy_no_comp = self._get_default_opts()
        fake_copy_no_comp['compressed_copy'] = 'no'

        valid_iogrp = self.driver._state['available_iogrps']
        self.driver._state['available_iogrps'] = [9999]
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)
        self.driver._state['available_iogrps'] = valid_iogrp

        get_vdisk_copy_attrs.side_effect = [fake_copy_thin,
                                            fake_copy_thick,
                                            fake_copy_no_comp,
                                            fake_copy_comp,
                                            fake_copy_thick,
                                            fake_copy_thick
                                            ]
        new_volume['volume_type_id'] = type_thick_ref['id']
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        new_volume['volume_type_id'] = type_thin_ref['id']
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        new_volume['volume_type_id'] = type_comp_ref['id']
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        new_volume['volume_type_id'] = type_thin_ref['id']
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        new_volume['volume_type_id'] = type_iogrp_ref['id']
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        new_volume['volume_type_id'] = type_thick_ref['id']
        no_exist_pool = 'i-dont-exist-%s' % random.randint(10000, 99999)
        new_volume['host'] = 'openstack@svc#%s' % no_exist_pool
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        self._reset_flags()
        volume_types.destroy(ctxt, type_thick_ref['id'])
        volume_types.destroy(ctxt, type_comp_ref['id'])
        volume_types.destroy(ctxt, type_iogrp_ref['id'])

    def test_manage_existing_good_uid_not_mapped(self):
        """Tests managing a volume with no mappings.

        This test case attempts to manage an existing volume by UID, and
        we expect it to succeed.  We verify that the backend volume was
        renamed to have the name of the Cinder volume that we asked for it to
        be associated with.
        """

        # Create a volume as a way of getting a vdisk created, and find out the
        # UID of that vdisk.
        _volume, uid = self._create_volume_and_return_uid('manage_test')

        # Descriptor of the Cinder volume that we want to own the vdisk
        # referenced by uid.
        new_volume = self._generate_vol_info()

        # Submit the request to manage it.
        ref = {'source-id': uid}
        size = self.driver.manage_existing_get_size(new_volume, ref)
        self.assertEqual(10, size)
        self.driver.manage_existing(new_volume, ref)

        # Assert that there is a disk named after the new volume that has the
        # ID that we passed in, indicating that the disk has been renamed.
        uid_of_new_volume = self._get_vdisk_uid(new_volume['name'])
        self.assertEqual(uid, uid_of_new_volume)

    def test_manage_existing_good_name_not_mapped(self):
        """Tests managing a volume with no mappings.

        This test case attempts to manage an existing volume by name, and
        we expect it to succeed.  We verify that the backend volume was
        renamed to have the name of the Cinder volume that we asked for it to
        be associated with.
        """

        # Create a volume as a way of getting a vdisk created, and find out the
        # UID of that vdisk.
        _volume, uid = self._create_volume_and_return_uid('manage_test')

        # Descriptor of the Cinder volume that we want to own the vdisk
        # referenced by uid.
        new_volume = self._generate_vol_info()

        # Submit the request to manage it.
        ref = {'source-name': _volume['name']}
        size = self.driver.manage_existing_get_size(new_volume, ref)
        self.assertEqual(10, size)
        self.driver.manage_existing(new_volume, ref)

        # Assert that there is a disk named after the new volume that has the
        # ID that we passed in, indicating that the disk has been renamed.
        uid_of_new_volume = self._get_vdisk_uid(new_volume['name'])
        self.assertEqual(uid, uid_of_new_volume)

    def test_manage_existing_mapped(self):
        """Tests managing a mapped volume with no override.

        This test case attempts to manage an existing volume by UID, but
        the volume is mapped to a host, so we expect to see an exception
        raised.
        """
        # Create a volume as a way of getting a vdisk created, and find out the
        # UUID of that vdisk.
        # Set replication target.
        volume, uid = self._create_volume_and_return_uid('manage_test')

        # Map a host to the disk
        conn = {'initiator': u'unicode:initiator3',
                'ip': '10.10.10.12',
                'host': u'unicode.foo}.bar}.baz'}
        self.driver.initialize_connection(volume, conn)

        # Descriptor of the Cinder volume that we want to own the vdisk
        # referenced by uid.
        volume = self._generate_vol_info()
        ref = {'source-id': uid}

        # Attempt to manage this disk, and except an exception beause the
        # volume is already mapped.
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing_get_size, volume, ref)

        ref = {'source-name': volume['name']}
        self.assertRaises(exception.ManageExistingInvalidReference,
                          self.driver.manage_existing_get_size, volume, ref)

    def test_manage_existing_good_uid_mapped_with_override(self):
        """Tests managing a mapped volume with override.

        This test case attempts to manage an existing volume by UID, when it
        already mapped to a host, but the ref specifies that this is OK.
        We verify that the backend volume was renamed to have the name of the
        Cinder volume that we asked for it to be associated with.
        """
        # Create a volume as a way of getting a vdisk created, and find out the
        # UUID of that vdisk.
        volume, uid = self._create_volume_and_return_uid('manage_test')

        # Map a host to the disk
        conn = {'initiator': u'unicode:initiator3',
                'ip': '10.10.10.12',
                'host': u'unicode.foo}.bar}.baz'}
        self.driver.initialize_connection(volume, conn)

        # Descriptor of the Cinder volume that we want to own the vdisk
        # referenced by uid.
        new_volume = self._generate_vol_info()

        # Submit the request to manage it, specifying that it is OK to
        # manage a volume that is already attached.
        ref = {'source-id': uid, 'manage_if_in_use': True}
        size = self.driver.manage_existing_get_size(new_volume, ref)
        self.assertEqual(10, size)
        self.driver.manage_existing(new_volume, ref)

        # Assert that there is a disk named after the new volume that has the
        # ID that we passed in, indicating that the disk has been renamed.
        uid_of_new_volume = self._get_vdisk_uid(new_volume['name'])
        self.assertEqual(uid, uid_of_new_volume)

    def test_manage_existing_good_name_mapped_with_override(self):
        """Tests managing a mapped volume with override.

        This test case attempts to manage an existing volume by name, when it
        already mapped to a host, but the ref specifies that this is OK.
        We verify that the backend volume was renamed to have the name of the
        Cinder volume that we asked for it to be associated with.
        """
        # Create a volume as a way of getting a vdisk created, and find out the
        # UUID of that vdisk.
        volume, uid = self._create_volume_and_return_uid('manage_test')

        # Map a host to the disk
        conn = {'initiator': u'unicode:initiator3',
                'ip': '10.10.10.12',
                'host': u'unicode.foo}.bar}.baz'}
        self.driver.initialize_connection(volume, conn)

        # Descriptor of the Cinder volume that we want to own the vdisk
        # referenced by uid.
        new_volume = self._generate_vol_info()

        # Submit the request to manage it, specifying that it is OK to
        # manage a volume that is already attached.
        ref = {'source-name': volume['name'], 'manage_if_in_use': True}
        size = self.driver.manage_existing_get_size(new_volume, ref)
        self.assertEqual(10, size)
        self.driver.manage_existing(new_volume, ref)

        # Assert that there is a disk named after the new volume that has the
        # ID that we passed in, indicating that the disk has been renamed.
        uid_of_new_volume = self._get_vdisk_uid(new_volume['name'])
        self.assertEqual(uid, uid_of_new_volume)


class CLIResponseTestCase(test.TestCase):
    def test_empty(self):
        self.assertEqual(0, len(
            storwize_svc_common.CLIResponse('')))
        self.assertEqual(0, len(
            storwize_svc_common.CLIResponse(('', 'stderr'))))

    def test_header(self):
        raw = r'''id!name
1!node1
2!node2
'''
        resp = storwize_svc_common.CLIResponse(raw, with_header=True)
        self.assertEqual(2, len(resp))
        self.assertEqual('1', resp[0]['id'])
        self.assertEqual('2', resp[1]['id'])

    def test_select(self):
        raw = r'''id!123
name!Bill
name!Bill2
age!30
home address!s1
home address!s2

id! 7
name!John
name!John2
age!40
home address!s3
home address!s4
'''
        resp = storwize_svc_common.CLIResponse(raw, with_header=False)
        self.assertEqual([('s1', 'Bill', 's1'), ('s2', 'Bill2', 's2'),
                          ('s3', 'John', 's3'), ('s4', 'John2', 's4')],
                         list(resp.select('home address', 'name',
                                          'home address')))

    def test_lsnode_all(self):
        raw = r'''id!name!UPS_serial_number!WWNN!status
1!node1!!500507680200C744!online
2!node2!!500507680200C745!online
'''
        resp = storwize_svc_common.CLIResponse(raw)
        self.assertEqual(2, len(resp))
        self.assertEqual('1', resp[0]['id'])
        self.assertEqual('500507680200C744', resp[0]['WWNN'])
        self.assertEqual('2', resp[1]['id'])
        self.assertEqual('500507680200C745', resp[1]['WWNN'])

    def test_lsnode_single(self):
        raw = r'''id!1
port_id!500507680210C744
port_status!active
port_speed!8Gb
port_id!500507680240C744
port_status!inactive
port_speed!8Gb
'''
        resp = storwize_svc_common.CLIResponse(raw, with_header=False)
        self.assertEqual(1, len(resp))
        self.assertEqual('1', resp[0]['id'])
        self.assertEqual([('500507680210C744', 'active'),
                          ('500507680240C744', 'inactive')],
                         list(resp.select('port_id', 'port_status')))


class StorwizeHelpersTestCase(test.TestCase):
    def setUp(self):
        super(StorwizeHelpersTestCase, self).setUp()
        self.storwize_svc_common = storwize_svc_common.StorwizeHelpers(None)
        self.mock_wait_time = mock.patch.object(
            storwize_svc_common.StorwizeHelpers, "WAIT_TIME", 0)

    @mock.patch.object(storwize_svc_common.StorwizeSSH, 'lslicense')
    @mock.patch.object(storwize_svc_common.StorwizeSSH, 'lsguicapabilities')
    def test_compression_enabled(self, lsguicapabilities, lslicense):
        fake_license_without_keys = {}
        fake_license = {
            'license_compression_enclosures': '1',
            'license_compression_capacity': '1'
        }
        fake_license_scheme = {
            'license_scheme': '9846'
        }
        fake_license_invalid_scheme = {
            'license_scheme': '0000'
        }

        lslicense.side_effect = [fake_license_without_keys,
                                 fake_license_without_keys,
                                 fake_license,
                                 fake_license_without_keys]
        lsguicapabilities.side_effect = [fake_license_without_keys,
                                         fake_license_invalid_scheme,
                                         fake_license_scheme]
        self.assertFalse(self.storwize_svc_common.compression_enabled())

        self.assertFalse(self.storwize_svc_common.compression_enabled())

        self.assertTrue(self.storwize_svc_common.compression_enabled())

        self.assertTrue(self.storwize_svc_common.compression_enabled())

    @mock.patch.object(storwize_svc_common.StorwizeSSH, 'lsguicapabilities')
    def test_replication_licensed(self, lsguicapabilities):
        lsguicapabilities.side_effect = [
            {'product_key': '0000'},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE_V3500},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE_V3700},
            {'product_key':
                storwize_const.DEV_MODEL_SVC},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE_V7000},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE_V5000},
            {'product_key':
                storwize_const.DEV_MODEL_STORWIZE_V5000_1YR},
            {'product_key':
                storwize_const.DEV_MODEL_FLASH_V9000},
            {'product_key':
                storwize_const.DEV_MODEL_FLEX}]
        for i in range(3):
            self.assertFalse(self.storwize_svc_common.replication_licensed())

        for i in range(7):
            self.assertTrue(self.storwize_svc_common.replication_licensed())

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_vdisk_count_by_io_group')
    def test_select_io_group(self, get_vdisk_count_by_io_group):
        # given io groups
        opts = {}
        # system io groups
        state = {}

        fake_iog_vdc1 = {0: 100, 1: 50, 2: 50, 3: 300}
        fake_iog_vdc2 = {0: 2, 1: 1, 2: 200}
        fake_iog_vdc3 = {0: 2, 2: 200}
        fake_iog_vdc4 = {0: 100, 1: 100, 2: 100, 3: 100}
        fake_iog_vdc5 = {0: 10, 1: 1, 2: 200, 3: 300}

        get_vdisk_count_by_io_group.side_effect = [fake_iog_vdc1,
                                                   fake_iog_vdc2,
                                                   fake_iog_vdc3,
                                                   fake_iog_vdc4,
                                                   fake_iog_vdc5]
        opts['iogrp'] = '0,2'
        state['available_iogrps'] = [0, 1, 2, 3]

        iog = self.storwize_svc_common.select_io_group(state, opts)
        self.assertTrue(iog in state['available_iogrps'])
        self.assertEqual(2, iog)

        opts['iogrp'] = '0'
        state['available_iogrps'] = [0, 1, 2]

        iog = self.storwize_svc_common.select_io_group(state, opts)
        self.assertTrue(iog in state['available_iogrps'])
        self.assertEqual(0, iog)

        opts['iogrp'] = '1,2'
        state['available_iogrps'] = [0, 2]

        iog = self.storwize_svc_common.select_io_group(state, opts)
        self.assertTrue(iog in state['available_iogrps'])
        self.assertEqual(2, iog)

        opts['iogrp'] = ' 0, 1, 2 '
        state['available_iogrps'] = [0, 1, 2, 3]

        iog = self.storwize_svc_common.select_io_group(state, opts)
        self.assertTrue(iog in state['available_iogrps'])
        # since vdisk count in all iogroups is same, it will pick the first
        self.assertEqual(0, iog)

        opts['iogrp'] = '0,1,2, 3'
        state['available_iogrps'] = [0, 1, 2, 3]

        iog = self.storwize_svc_common.select_io_group(state, opts)
        self.assertTrue(iog in state['available_iogrps'])
        self.assertEqual(1, iog)


@ddt.ddt
class StorwizeSSHTestCase(test.TestCase):
    def setUp(self):
        super(StorwizeSSHTestCase, self).setUp()
        self.fake_driver = StorwizeSVCISCSIFakeDriver(
            configuration=conf.Configuration(None))
        sim = StorwizeSVCManagementSimulator(['openstack'])
        self.fake_driver.set_fake_storage(sim)
        self.storwize_ssh = storwize_svc_common.StorwizeSSH(
            self.fake_driver._run_ssh)

    def test_mkvdiskhostmap(self):
        # mkvdiskhostmap should not be returning anything
        self.fake_driver.fake_storage._volumes_list['9999'] = {
            'name': ' 9999', 'id': '0', 'uid': '0',
            'IO_group_id': '0', 'IO_group_name': 'fakepool'}
        self.fake_driver.fake_storage._hosts_list['HOST1'] = {
            'name': 'HOST1', 'id': '0', 'host_name': 'HOST1'}
        self.fake_driver.fake_storage._hosts_list['HOST2'] = {
            'name': 'HOST2', 'id': '1', 'host_name': 'HOST2'}
        self.fake_driver.fake_storage._hosts_list['HOST3'] = {
            'name': 'HOST3', 'id': '2', 'host_name': 'HOST3'}

        ret = self.storwize_ssh.mkvdiskhostmap('HOST1', '9999', '511', False)
        self.assertEqual('511', ret)

        ret = self.storwize_ssh.mkvdiskhostmap('HOST2', '9999', '512', True)
        self.assertEqual('512', ret)

        ret = self.storwize_ssh.mkvdiskhostmap('HOST3', '9999', None, True)
        self.assertIsNotNone(ret)

        with mock.patch.object(
                storwize_svc_common.StorwizeSSH,
                'run_ssh_check_created') as run_ssh_check_created:
            ex = exception.VolumeBackendAPIException(data='CMMVC6071E')
            run_ssh_check_created.side_effect = ex
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.storwize_ssh.mkvdiskhostmap,
                              'HOST3', '9999', 511, True)

    @ddt.data((exception.VolumeBackendAPIException(data='CMMVC6372W'), None),
              (exception.VolumeBackendAPIException(data='CMMVC6372W'),
               {'name': 'fakevol', 'id': '0', 'uid': '0', 'IO_group_id': '0',
                'IO_group_name': 'fakepool'}),
              (exception.VolumeBackendAPIException(data='error'), None))
    @ddt.unpack
    def test_mkvdisk_with_warning(self, run_ssh_check, lsvol):
        opt = {'iogrp': 0}
        with mock.patch.object(storwize_svc_common.StorwizeSSH,
                               'run_ssh_check_created',
                               side_effect=run_ssh_check),\
            mock.patch.object(storwize_svc_common.StorwizeSSH, 'lsvdisk',
                              return_value=lsvol):
            if lsvol:
                ret = self.storwize_ssh.mkvdisk('fakevol', '1', 'gb',
                                                'fakepool', opt, [])
                self.assertEqual('0', ret)
            else:
                self.assertRaises(exception.VolumeBackendAPIException,
                                  self.storwize_ssh.mkvdisk,
                                  'fakevol', '1', 'gb', 'fakepool', opt, [])


@ddt.ddt
class StorwizeSVCReplicationTestCase(test.TestCase):
    @mock.patch.object(time, 'sleep')
    def setUp(self, mock_sleep):
        super(StorwizeSVCReplicationTestCase, self).setUp()

        def _run_ssh_aux(cmd, check_exit_code=True, attempts=1):
            utils.check_ssh_injection(cmd)
            if len(cmd) > 2 and cmd[1] == 'lssystem':
                cmd[1] = 'lssystem_aux'
            ret = self.sim.execute_command(cmd, check_exit_code)
            return ret
        aux_connect_patcher = mock.patch(
            'cinder.volume.drivers.ibm.storwize_svc.'
            'replication.StorwizeSVCReplicationManager._run_ssh')
        self.aux_ssh_mock = aux_connect_patcher.start()
        self.addCleanup(aux_connect_patcher.stop)
        self.aux_ssh_mock.side_effect = _run_ssh_aux

        self.USESIM = True
        if self.USESIM:
            self.driver = StorwizeSVCFcFakeDriver(
                configuration=conf.Configuration(None))
            self.rep_target = {"backend_id": "svc_aux_target_1",
                               "san_ip": "192.168.10.22",
                               "san_login": "admin",
                               "san_password": "admin",
                               "pool_name": _get_test_pool()}
            self.fake_target = {"backend_id": "svc_id_target",
                                "san_ip": "192.168.10.23",
                                "san_login": "admin",
                                "san_password": "admin",
                                "pool_name": _get_test_pool()}
            self._def_flags = {'san_ip': '192.168.10.21',
                               'san_login': 'user',
                               'san_password': 'pass',
                               'storwize_svc_volpool_name':
                               SVC_POOLS,
                               'replication_device': [self.rep_target]}
            wwpns = [
                six.text_type(random.randint(0, 9999999999999999)).zfill(16),
                six.text_type(random.randint(0, 9999999999999999)).zfill(16)]
            initiator = 'test.initiator.%s' % six.text_type(
                random.randint(10000, 99999))
            self._connector = {'ip': '1.234.56.78',
                               'host': 'storwize-svc-test',
                               'wwpns': wwpns,
                               'initiator': initiator}
            self.sim = StorwizeSVCManagementSimulator(SVC_POOLS)

            self.driver.set_fake_storage(self.sim)
            self.ctxt = context.get_admin_context()

        self._reset_flags()
        self.ctxt = context.get_admin_context()
        db_driver = self.driver.configuration.db_driver
        self.db = importutils.import_module(db_driver)
        self.driver.db = self.db

        self.driver.do_setup(None)
        self.driver.check_for_setup_error()
        self._create_test_volume_types()

    def _set_flag(self, flag, value):
        group = self.driver.configuration.config_group
        self.driver.configuration.set_override(flag, value, group)

    def _reset_flags(self):
        self.driver.configuration.local_conf.reset()
        for k, v in self._def_flags.items():
            self._set_flag(k, v)
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])

    def _assert_vol_exists(self, name, exists):
        is_vol_defined = self.driver._helpers.is_vdisk_defined(name)
        self.assertEqual(exists, is_vol_defined)

    def _generate_vol_info(self, vol_type=None, size=1):
        pool = _get_test_pool()
        volume_type = vol_type if vol_type else self.non_replica_type
        prop = {'size': size,
                'volume_type_id': volume_type.id,
                'host': 'openstack@svc#%s' % pool
                }
        vol = testutils.create_volume(self.ctxt, **prop)
        return vol

    def _generate_snap_info(self, vol_id):
        prop = {'volume_id': vol_id}
        snap = testutils.create_snapshot(self.ctxt, **prop)
        return snap

    def _create_replica_volume_type(self, enable,
                                    rep_type=storwize_const.METRO,
                                    opts=None, vol_type_name=None):
        # Generate a volume type for volume repliation.
        if enable:
            if rep_type == storwize_const.METRO:
                spec = {'replication_enabled': '<is> True',
                        'replication_type': '<in> metro'}
                type_name = 'rep_metro'
            else:
                spec = {'replication_enabled': '<is> True',
                        'replication_type': '<in> global'}
                type_name = 'rep_global'
        elif opts:
            spec = opts
            type_name = vol_type_name
        else:
            spec = {'replication_enabled': '<is> False'}
            type_name = "non_rep"

        type_ref = volume_types.create(self.ctxt, type_name, spec)
        replication_type = objects.VolumeType.get_by_id(self.ctxt,
                                                        type_ref['id'])
        return replication_type

    def _create_test_volume_types(self):
        self.mm_type = self._create_replica_volume_type(
            True, rep_type=storwize_const.METRO)
        self.gm_type = self._create_replica_volume_type(
            True, rep_type=storwize_const.GLOBAL)
        self.non_replica_type = self._create_replica_volume_type(False)

    def _create_test_volume(self, rep_type):
        volume = self._generate_vol_info(rep_type)
        model_update = self.driver.create_volume(volume)
        return volume, model_update

    def _get_vdisk_uid(self, vdisk_name):
        vdisk_properties, _err = self.sim._cmd_lsvdisk(obj=vdisk_name,
                                                       delim='!')
        for row in vdisk_properties.split('\n'):
            words = row.split('!')
            if words[0] == 'vdisk_UID':
                return words[1]
        return None

    def test_storwize_do_replication_setup_error(self):
        fake_targets = [self.rep_target, self.rep_target]
        self.driver.configuration.set_override('replication_device',
                                               [{"backend_id":
                                                "svc_id_target"}])
        self.assertRaises(exception.InvalidInput,
                          self.driver._do_replication_setup)

        self.driver.configuration.set_override('replication_device',
                                               fake_targets)
        self.assertRaises(exception.InvalidInput,
                          self.driver._do_replication_setup)

        self.driver._active_backend_id = 'fake_id'
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.assertRaises(exception.InvalidInput,
                          self.driver._do_replication_setup)

        self.driver._active_backend_id = None

        self.driver._do_replication_setup()
        self.assertEqual(self.driver._replica_target, self.rep_target)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'replication_licensed')
    def test_storwize_setup_replication(self,
                                        replication_licensed):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver._active_backend_id = None
        replication_licensed.side_effect = [False, True, True, True]

        self.driver._get_storwize_config()
        self.assertEqual(self.driver._helpers,
                         self.driver._master_backend_helpers)
        self.assertEqual(self.driver._replica_enabled, False)

        self.driver._get_storwize_config()
        self.assertEqual(self.driver._replica_target, self.rep_target)
        self.assertEqual(self.driver._replica_enabled, True)

        self.driver._active_backend_id = self.rep_target['backend_id']
        self.driver._get_storwize_config()
        self.assertEqual(self.driver._helpers,
                         self.driver._aux_backend_helpers)
        self.assertEqual(self.driver._replica_enabled, True)

        self.driver._active_backend_id = None
        self.driver._get_storwize_config()

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'create_vdisk')
    def test_storwize_svc_create_stretch_volume_with_replication(self,
                                                                 create_vdisk):
        spec = {'mirror_pool': 'openstack1',
                'replication_enabled': '<is> True',
                'replication_type': '<in> global'
                }
        vol_type = self._create_replica_volume_type(
            False, opts=spec, vol_type_name='test_type')
        vol = self._generate_vol_info(vol_type)
        self.assertRaises(exception.InvalidInput,
                          self.driver.create_volume, vol)
        self.assertFalse(create_vdisk.called)

    def test_storwize_create_volume_with_mirror_replication(self):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(volume)
        self.driver.delete_volume(volume)
        self._validate_replic_vol_deletion(volume)

        # Create global mirror replication.
        volume, model_update = self._create_test_volume(self.gm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(volume)
        self.driver.delete_volume(volume)
        self._validate_replic_vol_deletion(volume)

    def _validate_replic_vol_creation(self, volume):
        # Create metro mirror volume
        self._assert_vol_exists(volume['name'], True)
        self._assert_vol_exists(
            storwize_const.REPLICA_AUX_VOL_PREFIX + volume['name'], True)

        rel_info = self.driver._helpers.get_relationship_info(volume['name'])
        self.assertIsNotNone(rel_info)
        vol_rep_type = rel_info['copy_type']
        rep_type = self.driver._get_volume_replicated_type(self.ctxt, volume)
        self.assertEqual(vol_rep_type, rep_type)

        self.assertEqual(rel_info['primary'], 'master')
        self.assertEqual(rel_info['master_vdisk_name'], volume['name'])
        self.assertEqual(
            rel_info['aux_vdisk_name'],
            storwize_const.REPLICA_AUX_VOL_PREFIX + volume['name'])
        self.assertEqual(rel_info['state'], 'inconsistent_copying')

        self.sim._rc_state_transition('wait', rel_info)
        self.assertEqual(rel_info['state'], 'consistent_synchronized')

    def _validate_replic_vol_deletion(self, volume):
        self._assert_vol_exists(volume['name'], False)
        self._assert_vol_exists(
            storwize_const.REPLICA_AUX_VOL_PREFIX + volume['name'], False)
        rel_info = self.driver._helpers.get_relationship_info(volume['name'])
        self.assertIsNone(rel_info)

    def test_storwize_create_snapshot_volume_with_mirror_replica(self):
        # Set replication target
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication volume.
        vol1, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        snap = testutils.create_snapshot(self.ctxt, vol1.id)
        self.driver.create_snapshot(snap)

        vol2 = self._generate_vol_info(self.mm_type)
        model_update = self.driver.create_volume_from_snapshot(vol2, snap)
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(vol2)

        self.driver.delete_snapshot(snap)
        self.driver.delete_volume(vol1)
        self.driver.delete_volume(vol2)

    def test_storwize_create_cloned_volume_with_mirror_replica(self):
        # Set replication target
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create a source metro mirror replication volume.
        src_volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        volume = self._generate_vol_info(self.mm_type)

        # Create a cloned volume from source volume.
        model_update = self.driver.create_cloned_volume(volume, src_volume)
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(volume)

        self.driver.delete_volume(src_volume)
        self.driver.delete_volume(volume)

    @ddt.data(({'replication_enabled': '<is> True',
                'replication_type': '<in> global'},
               {'replication_enabled': '<is> True',
                'replication_type': '<in> metro'}),
              ({'replication_enabled': '<is> True',
                'replication_type': '<in> metro'},
               {'replication_enabled': '<is> True',
                'replication_type': '<in> global'}),
              ({'replication_enabled': '<is> True',
                'replication_type': '<in> metro'},
               {'mirror_pool': 'openstack1'}),
              ({'mirror_pool': 'openstack1'},
               {'mirror_pool': 'openstack1',
                'replication_enabled': '<is> True',
                'replication_type': '<in> metro'}),
              ({'replication_enabled': '<is> False'},
               {'mirror_pool': 'openstack1',
                'replication_enabled': '<is> True',
                'replication_type': '<in> metro'}))
    @ddt.unpack
    def test_storwize_retype_invalid_replication(self, old_opts, new_opts):
        # Set replication target
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}
        old_type = self._create_replica_volume_type(
            False, opts=old_opts, vol_type_name='test_old_type')

        volume, model_update = self._create_test_volume(old_type)
        new_type = self._create_replica_volume_type(
            False, opts=new_opts, vol_type_name='test_new_type')
        diff, _equal = volume_types.volume_types_diff(
            self.ctxt, new_type['id'], old_type['id'])
        self.assertRaises(exception.VolumeDriverException, self.driver.retype,
                          self.ctxt, volume, new_type, diff, host)

    def test_storwize_retype_from_mirror_to_none_replication(self):
        # Set replication target
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}

        volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        diff, _equal = volume_types.volume_types_diff(
            self.ctxt, self.non_replica_type['id'], self.mm_type['id'])
        # Disable replica
        retyped, model_update = self.driver.retype(
            self.ctxt, volume, self.non_replica_type, diff, host)
        self.assertEqual('disabled', model_update['replication_status'])
        self._assert_vol_exists(
            storwize_const.REPLICA_AUX_VOL_PREFIX + volume['name'], False)

        self.driver.delete_volume(volume)
        self._assert_vol_exists(volume['name'], False)
        rel_info = self.driver._helpers.get_relationship_info(volume['name'])
        self.assertIsNone(rel_info)

    def test_storwize_retype_from_none_to_mirror_replication(self):
        # Set replication target
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)
        host = {'host': 'openstack@svc#openstack'}

        diff, _equal = volume_types.volume_types_diff(
            self.ctxt, self.non_replica_type['id'], self.mm_type['id'])

        volume, model_update = self._create_test_volume(self.non_replica_type)
        self.assertIsNone(model_update)

        # Enable replica
        retyped, model_update = self.driver.retype(
            self.ctxt, volume, self.mm_type, diff, host)
        volume['volume_type_id'] = self.mm_type['id']
        volume['volume_type'] = self.mm_type
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(volume)

        self.driver.delete_volume(volume)

    def test_storwize_extend_volume_replication(self):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        self.driver.extend_volume(volume, '13')
        attrs = self.driver._helpers.get_vdisk_attributes(volume['name'])
        vol_size = int(attrs['capacity']) / units.Gi
        self.assertAlmostEqual(vol_size, 13)

        attrs = self.driver._aux_backend_helpers.get_vdisk_attributes(
            storwize_const.REPLICA_AUX_VOL_PREFIX + volume['name'])
        vol_size = int(attrs['capacity']) / units.Gi
        self.assertAlmostEqual(vol_size, 13)

        self.driver.delete_volume(volume)
        self._validate_replic_vol_deletion(volume)

    def test_storwize_manage_existing_mismatch_with_volume_replication(self):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create replication volume.
        rep_volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        # Create non-replication volume.
        non_rep_volume, model_update = self._create_test_volume(
            self.non_replica_type)

        new_volume = self._generate_vol_info()

        ref = {'source-name': rep_volume['name']}
        new_volume['volume_type_id'] = self.non_replica_type['id']
        new_volume['volume_type'] = self.non_replica_type
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        ref = {'source-name': non_rep_volume['name']}
        new_volume['volume_type_id'] = self.mm_type['id']
        new_volume['volume_type'] = self.mm_type
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)

        ref = {'source-name': rep_volume['name']}
        new_volume['volume_type_id'] = self.gm_type['id']
        new_volume['volume_type'] = self.gm_type
        self.assertRaises(exception.ManageExistingVolumeTypeMismatch,
                          self.driver.manage_existing, new_volume, ref)
        self.driver.delete_volume(rep_volume)
        self.driver.delete_volume(new_volume)

    def test_storwize_manage_existing_with_volume_replication(self):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create replication volume.
        rep_volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        uid_of_master = self._get_vdisk_uid(rep_volume['name'])
        uid_of_aux = self._get_vdisk_uid(
            storwize_const.REPLICA_AUX_VOL_PREFIX + rep_volume['name'])

        new_volume = self._generate_vol_info()
        ref = {'source-name': rep_volume['name']}
        new_volume['volume_type_id'] = self.mm_type['id']
        new_volume['volume_type'] = self.mm_type
        self.driver.manage_existing(new_volume, ref)

        # Check the uid of the volume which has been renamed.
        uid_of_master_volume = self._get_vdisk_uid(new_volume['name'])
        uid_of_aux_volume = self._get_vdisk_uid(
            storwize_const.REPLICA_AUX_VOL_PREFIX + new_volume['name'])
        self.assertEqual(uid_of_master, uid_of_master_volume)
        self.assertEqual(uid_of_aux, uid_of_aux_volume)

        self.driver.delete_volume(rep_volume)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers, 'rename_vdisk')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_storwize_update_migrated_replication_volume(
            self, get_rp_info, rename_vdisk):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create replication volume.
        backend_volume, model_update = self._create_test_volume(self.mm_type)
        volume, model_update = self._create_test_volume(self.mm_type)
        get_rp_info.side_effect = [{'aux_vdisk_name': 'aux_test'}]
        model_update = self.driver.update_migrated_volume(self.ctxt, volume,
                                                          backend_volume,
                                                          'available')
        aux_vol = (storwize_const.REPLICA_AUX_VOL_PREFIX + volume.name)
        rename_vdisk.assert_called_with('aux_test', aux_vol)
        self.assertEqual({'_name_id': None}, model_update)

        rename_vdisk.reset_mock()
        rename_vdisk.side_effect = exception.VolumeBackendAPIException
        model_update = self.driver.update_migrated_volume(self.ctxt, volume,
                                                          backend_volume,
                                                          'available')
        self.assertEqual({'_name_id': backend_volume.id}, model_update)

    def test_storwize_delete_volume_with_mirror_replication(self):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        volume, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        self._validate_replic_vol_creation(volume)

        # Delete volume in non-failover state
        self.driver.delete_volume(volume)
        self._validate_replic_vol_deletion(volume)

        non_replica_vol, model_update = self._create_test_volume(
            self.non_replica_type)
        self.assertIsNone(model_update)

        volumes = [volume, non_replica_vol]
        # Delete volume in failover state
        self.driver.failover_host(
            self.ctxt, volumes, self.rep_target['backend_id'])
        # Delete non-replicate volume in a failover state
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.delete_volume,
                          non_replica_vol)

        # Delete replicate volume in failover state
        self.driver.delete_volume(volume)
        self._validate_replic_vol_deletion(volume)
        self.driver.failover_host(
            self.ctxt, volumes, 'default')
        self.driver.delete_volume(non_replica_vol)
        self._assert_vol_exists(non_replica_vol['name'], False)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_vdisk')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_delete_target_volume(self, get_relationship_info,
                                  delete_relationship,
                                  delete_vdisk):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)
        fake_name = 'volume-%s' % fake.VOLUME_ID
        get_relationship_info.return_value = {'aux_vdisk_name':
                                              fake_name}
        self.driver._helpers.delete_rc_volume(fake_name)
        get_relationship_info.assert_called_once_with(fake_name)
        delete_relationship.assert_called_once_with(fake_name)
        delete_vdisk.assert_called_once_with(fake_name, False)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_vdisk')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_delete_target_volume_no_relationship(self, get_relationship_info,
                                                  delete_relationship,
                                                  delete_vdisk):
        # Set replication target.
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)
        fake_name = 'volume-%s' % fake.VOLUME_ID
        get_relationship_info.return_value = None
        self.driver._helpers.delete_rc_volume(fake_name)
        get_relationship_info.assert_called_once_with(fake_name)
        self.assertFalse(delete_relationship.called)
        self.assertTrue(delete_vdisk.called)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_vdisk')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'delete_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_delete_target_volume_fail(self, get_relationship_info,
                                       delete_relationship,
                                       delete_vdisk):
        fake_id = fake.VOLUME_ID
        fake_name = 'volume-%s' % fake_id
        get_relationship_info.return_value = {'aux_vdisk_name':
                                              fake_name}
        delete_vdisk.side_effect = Exception
        self.assertRaises(exception.VolumeDriverException,
                          self.driver._helpers.delete_rc_volume,
                          fake_name)
        get_relationship_info.assert_called_once_with(fake_name)
        delete_relationship.assert_called_once_with(fake_name)

    def test_storwize_failover_host_backend_error(self):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        mm_vol, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        volumes = [mm_vol]

        self.driver._replica_enabled = False
        self.assertRaises(exception.UnableToFailOver,
                          self.driver.failover_host,
                          self.ctxt, volumes, self.rep_target['backend_id'])
        self.driver._replica_enabled = True
        self.assertRaises(exception.InvalidReplicationTarget,
                          self.driver.failover_host,
                          self.ctxt, volumes, self.fake_target['backend_id'])

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_system_info') as get_sys_info:
            get_sys_info.side_effect = [
                exception.VolumeBackendAPIException(data='CMMVC6071E'),
                exception.VolumeBackendAPIException(data='CMMVC6071E')]
            self.assertRaises(exception.UnableToFailOver,
                              self.driver.failover_host,
                              self.ctxt, volumes,
                              self.rep_target['backend_id'])

            self.driver._active_backend_id = self.rep_target['backend_id']
            self.assertRaises(exception.UnableToFailOver,
                              self.driver.failover_host,
                              self.ctxt, volumes, 'default')
        self.driver.delete_volume(mm_vol)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_failover_volume_relationship_error(self, get_relationship_info):
        # Create global mirror replication.
        gm_vol, model_update = self._create_test_volume(self.gm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        get_relationship_info.side_effect = [None,
                                             exception.VolumeDriverException]
        expected_list = [{'updates': {'replication_status':
                                      'error_failing-over',
                                      'status': 'error'},
                          'volume_id': gm_vol['id']}
                         ]
        volumes_update = self.driver._failover_replica_volumes(self.ctxt,
                                                               [gm_vol])
        self.assertEqual(expected_list, volumes_update)

        volumes_update = self.driver._failover_replica_volumes(self.ctxt,
                                                               [gm_vol])
        self.assertEqual(expected_list, volumes_update)

    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_volume_stats')
    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_storwize_state')
    def test_storwize_failover_host_replica_volumes(self,
                                                    update_storwize_state,
                                                    update_volume_stats):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        mm_vol, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        # Create global replication volume.
        gm_vol, model_update = self._create_test_volume(self.gm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        volumes = [mm_vol, gm_vol]
        expected_list = [{'updates': {'replication_status': 'failed-over'},
                          'volume_id': mm_vol['id']},
                         {'updates': {'replication_status': 'failed-over'},
                          'volume_id': gm_vol['id']}
                         ]

        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, self.rep_target['backend_id'])
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual(expected_list, volume_list)

        self.assertEqual(self.driver._active_backend_id, target_id)
        self.assertEqual(self.driver._aux_backend_helpers,
                         self.driver._helpers)
        self.assertEqual([self.driver._replica_target['pool_name']],
                         self.driver._get_backend_pools())
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)

        self.driver.delete_volume(mm_vol)
        self.driver.delete_volume(gm_vol)

        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, None)
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual([], volume_list)

    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_volume_stats')
    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_storwize_state')
    def test_storwize_failover_host_normal_volumes(self,
                                                   update_storwize_state,
                                                   update_volume_stats):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        mm_vol, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        mm_vol['status'] = 'in-use'

        # Create non-replication volume.
        non_replica_vol, model_update = self._create_test_volume(
            self.non_replica_type)
        self.assertIsNone(model_update)
        non_replica_vol['status'] = 'error'

        volumes = [mm_vol, non_replica_vol]

        rep_data1 = json.dumps({'previous_status': mm_vol['status']})
        rep_data2 = json.dumps({'previous_status': non_replica_vol['status']})
        expected_list = [{'updates': {'status': 'error',
                                      'replication_driver_data': rep_data1},
                          'volume_id': mm_vol['id']},
                         {'updates': {'status': 'error',
                                      'replication_driver_data': rep_data2},
                          'volume_id': non_replica_vol['id']},
                         ]

        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, self.rep_target['backend_id'])
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual(expected_list, volume_list)

        self.assertEqual(self.driver._active_backend_id, target_id)
        self.assertEqual(self.driver._aux_backend_helpers,
                         self.driver._helpers)
        self.assertEqual([self.driver._replica_target['pool_name']],
                         self.driver._get_backend_pools())
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)

        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, None)
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual([], volume_list)
        # Delete non-replicate volume in a failover state
        self.assertRaises(exception.VolumeDriverException,
                          self.driver.delete_volume,
                          non_replica_vol)
        self.driver.failover_host(self.ctxt, volumes, 'default')
        self.driver.delete_volume(mm_vol)
        self.driver.delete_volume(non_replica_vol)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'switch_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'stop_relationship')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    def test_failover_host_by_force_access(self, get_relationship_info,
                                           stop_relationship,
                                           switch_relationship):
        replica_obj = self.driver._get_replica_obj(storwize_const.METRO)
        fake_vol = {'id': '21345678-1234-5678-1234-567812345683',
                    'name': 'fake-volume'}
        target_vol = storwize_const.REPLICA_AUX_VOL_PREFIX + fake_vol['name']
        context = mock.Mock
        get_relationship_info.side_effect = [{
            'aux_vdisk_name': 'replica-12345678-1234-5678-1234-567812345678',
            'name': 'RC_name'}]
        switch_relationship.side_effect = exception.VolumeDriverException
        replica_obj.failover_volume_host(context, fake_vol)
        get_relationship_info.assert_called_once_with(target_vol)
        switch_relationship.assert_called_once_with('RC_name')
        stop_relationship.assert_called_once_with(target_vol, access=True)

    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_volume_stats')
    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_storwize_state')
    def test_storwize_failback_replica_volumes(self,
                                               update_storwize_state,
                                               update_volume_stats):
        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create metro mirror replication.
        mm_vol, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        # Create global mirror replication.
        gm_vol, model_update = self._create_test_volume(self.gm_type)
        self.assertEqual('enabled', model_update['replication_status'])

        volumes = [gm_vol, mm_vol]
        failover_expect = [{'updates': {'replication_status': 'failed-over'},
                            'volume_id': gm_vol['id']},
                           {'updates': {'replication_status': 'failed-over'},
                            'volume_id': mm_vol['id']}
                           ]

        failback_expect = [{'updates': {'replication_status': 'enabled',
                                        'status': 'available'},
                            'volume_id': gm_vol['id']},
                           {'updates': {'replication_status': 'enabled',
                                        'status': 'available'},
                            'volume_id': mm_vol['id']},
                           ]
        # Already failback
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, 'default')
        self.assertIsNone(target_id)
        self.assertEqual([], volume_list)

        # fail over operation
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, self.rep_target['backend_id'])
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual(failover_expect, volume_list)
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)

        # fail back operation
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, 'default')
        self.assertEqual('default', target_id)
        self.assertEqual(failback_expect, volume_list)
        self.assertIsNone(self.driver._active_backend_id)
        self.assertEqual(SVC_POOLS, self.driver._get_backend_pools())
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)
        self.driver.delete_volume(mm_vol)
        self.driver.delete_volume(gm_vol)

    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_volume_stats')
    @mock.patch.object(storwize_svc_common.StorwizeSVCCommonDriver,
                       '_update_storwize_state')
    def test_storwize_failback_normal_volumes(self,
                                              update_storwize_state,
                                              update_volume_stats):

        self.driver.configuration.set_override('replication_device',
                                               [self.rep_target])
        self.driver.do_setup(self.ctxt)

        # Create replication volume.
        mm_vol, model_update = self._create_test_volume(self.mm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        mm_vol['status'] = 'in-use'
        gm_vol, model_update = self._create_test_volume(self.gm_type)
        self.assertEqual('enabled', model_update['replication_status'])
        gm_vol['status'] = 'available'

        # Create non-replication volume.
        non_replica_vol1, model_update = self._create_test_volume(
            self.non_replica_type)
        self.assertIsNone(model_update)
        non_replica_vol2, model_update = self._create_test_volume(
            self.non_replica_type)
        self.assertIsNone(model_update)
        non_replica_vol1['status'] = 'error'
        non_replica_vol2['status'] = 'available'

        volumes = [mm_vol, non_replica_vol1, non_replica_vol2, gm_vol]

        rep_data0 = json.dumps({'previous_status': mm_vol['status']})
        rep_data1 = json.dumps({'previous_status': non_replica_vol1['status']})
        rep_data2 = json.dumps({'previous_status': non_replica_vol2['status']})
        failover_expect = [{'updates': {'replication_status': 'failed-over'},
                            'volume_id': gm_vol['id']},
                           {'updates': {'status': 'error',
                                        'replication_driver_data': rep_data0},
                            'volume_id': mm_vol['id']},
                           {'updates': {'status': 'error',
                                        'replication_driver_data': rep_data1},
                            'volume_id': non_replica_vol1['id']},
                           {'updates': {'status': 'error',
                                        'replication_driver_data': rep_data2},
                            'volume_id': non_replica_vol2['id']}]
        # Already failback
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, 'default')
        self.assertIsNone(target_id)
        self.assertEqual([], volume_list)

        # fail over operation
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, self.rep_target['backend_id'])
        self.assertEqual(self.rep_target['backend_id'], target_id)
        self.assertEqual(failover_expect, volume_list)
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)

        # fail back operation
        mm_vol['replication_driver_data'] = json.dumps(
            {'previous_status': 'in-use'})
        non_replica_vol1['replication_driver_data'] = json.dumps(
            {'previous_status': 'error'})
        non_replica_vol2['replication_driver_data'] = json.dumps(
            {'previous_status': 'available'})
        gm_vol['status'] = 'in-use'
        rep_data3 = json.dumps({'previous_status': gm_vol['status']})
        failback_expect = [{'updates': {'status': 'in-use',
                                        'replication_driver_data': ''},
                            'volume_id': mm_vol['id']},
                           {'updates': {'status': 'error',
                                        'replication_driver_data': ''},
                            'volume_id': non_replica_vol1['id']},
                           {'updates': {'status': 'available',
                                        'replication_driver_data': ''},
                            'volume_id': non_replica_vol2['id']},
                           {'updates': {'status': 'error',
                                        'replication_driver_data': rep_data3},
                            'volume_id': gm_vol['id']}]
        target_id, volume_list = self.driver.failover_host(
            self.ctxt, volumes, 'default')
        self.assertEqual('default', target_id)
        self.assertEqual(failback_expect, volume_list)
        self.assertIsNone(self.driver._active_backend_id)
        self.assertEqual(SVC_POOLS, self.driver._get_backend_pools())
        self.assertTrue(update_storwize_state.called)
        self.assertTrue(update_volume_stats.called)
        self.driver.delete_volume(mm_vol)
        self.driver.delete_volume(non_replica_vol1)
        self.driver.delete_volume(non_replica_vol2)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_system_info')
    @mock.patch.object(storwize_rep.StorwizeSVCReplicationManager,
                       '_partnership_validate_create')
    def test_establish_partnership_with_local_sys(self, partnership_create,
                                                  get_system_info):
        get_system_info.side_effect = [{'system_name': 'storwize-svc-sim'},
                                       {'system_name': 'storwize-svc-sim'}]

        rep_mgr = self.driver._get_replica_mgr()
        rep_mgr.establish_target_partnership()
        self.assertFalse(partnership_create.called)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_system_info')
    def test_establish_target_partnership(self, get_system_info):
        source_system_name = 'storwize-svc-sim'
        target_system_name = 'aux-svc-sim'

        get_system_info.side_effect = [{'system_name': source_system_name},
                                       {'system_name': target_system_name}]

        rep_mgr = self.driver._get_replica_mgr()
        rep_mgr.establish_target_partnership()
        partner_info = self.driver._helpers.get_partnership_info(
            source_system_name)
        self.assertIsNotNone(partner_info)
        self.assertEqual(partner_info['name'], source_system_name)

        partner_info = self.driver._helpers.get_partnership_info(
            source_system_name)
        self.assertIsNotNone(partner_info)
        self.assertEqual(partner_info['name'], source_system_name)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_partnership_info')
    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'chpartnership')
    def test_start_partnership(self, chpartnership, get_partnership_info):
        get_partnership_info.side_effect = [
            None,
            {'partnership': 'fully_configured',
             'id': '0'},
            {'partnership': 'fully_configured_stopped',
             'id': '0'}]

        rep_mgr = self.driver._get_replica_mgr()
        rep_mgr._partnership_start(rep_mgr._master_helpers,
                                   'storwize-svc-sim')
        self.assertFalse(chpartnership.called)
        rep_mgr._partnership_start(rep_mgr._master_helpers,
                                   'storwize-svc-sim')
        self.assertFalse(chpartnership.called)

        rep_mgr._partnership_start(rep_mgr._master_helpers,
                                   'storwize-svc-sim')
        chpartnership.assert_called_once_with('0')

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'start_relationship')
    def test_sync_replica_volumes_with_aux(self, start_relationship):
        # Create metro mirror replication.
        mm_vol = self._generate_vol_info(self.mm_type)
        tgt_volume = storwize_const.REPLICA_AUX_VOL_PREFIX + mm_vol['name']

        volumes = [mm_vol]
        fake_info = {'volume': 'fake',
                     'master_vdisk_name': 'fake',
                     'aux_vdisk_name': 'fake'}
        sync_state = {'state': storwize_const.REP_CONSIS_SYNC,
                      'primary': 'fake'}
        sync_state.update(fake_info)
        disconn_state = {'state': storwize_const.REP_IDL_DISC,
                         'primary': 'master'}
        disconn_state.update(fake_info)
        stop_state = {'state': storwize_const.REP_CONSIS_STOP,
                      'primary': 'aux'}
        stop_state.update(fake_info)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=None)):
            self.driver._sync_with_aux(self.ctxt, volumes)
            self.assertFalse(start_relationship.called)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=sync_state)):
            self.driver._sync_with_aux(self.ctxt, volumes)
            self.assertFalse(start_relationship.called)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=disconn_state)):
            self.driver._sync_with_aux(self.ctxt, volumes)
            start_relationship.assert_called_once_with(tgt_volume)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=stop_state)):
            self.driver._sync_with_aux(self.ctxt, volumes)
            start_relationship.assert_called_with(tgt_volume,
                                                  primary='aux')
        self.driver.delete_volume(mm_vol)

    @mock.patch.object(storwize_svc_common.StorwizeHelpers,
                       'get_relationship_info')
    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall',
                new=testutils.ZeroIntervalLoopingCall)
    def test_wait_replica_vol_ready(self, get_relationship_info):
        # Create metro mirror replication.
        mm_vol = self._generate_vol_info(self.mm_type)
        fake_info = {'volume': 'fake',
                     'master_vdisk_name': 'fake',
                     'aux_vdisk_name': 'fake',
                     'primary': 'fake'}
        sync_state = {'state': storwize_const.REP_CONSIS_SYNC}
        sync_state.update(fake_info)
        disconn_state = {'state': storwize_const.REP_IDL_DISC}
        disconn_state.update(fake_info)
        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=None)):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver._wait_replica_vol_ready,
                              self.ctxt, mm_vol)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=sync_state)):
            self.driver._wait_replica_vol_ready(self.ctxt, mm_vol)

        with mock.patch.object(storwize_svc_common.StorwizeHelpers,
                               'get_relationship_info',
                               mock.Mock(return_value=disconn_state)):
            self.assertRaises(exception.VolumeBackendAPIException,
                              self.driver._wait_replica_vol_ready,
                              self.ctxt, mm_vol)
