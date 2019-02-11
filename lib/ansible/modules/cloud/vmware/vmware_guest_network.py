#!/usr/bin/python
#  -*- coding: utf-8 -*-
#  Copyright: (c) 2018, Ansible Project
#  Copyright: (c) 2018, Diane Wang <dianew@vmware.com>
#  GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: vmware_guest_network
short_description: Manage network adapters of specified virtual machine in given vCenter infrastructure
description:
    - This module is used to reconfigure network adapter settings of given virtual machine.
    - All parameters and VMware object names are case sensitive.
version_added: '2.8'
author:
    - Diane Wang (@Tomorrow9) <dianew@vmware.com>
notes:
    - Tested on vSphere 6.0, 6.5 and 6.7
requirements:
    - "python >= 2.6"
    - PyVmomi
options:
   name:
     description:
     - Name of the virtual machine.
     - This is a required parameter, if parameter C(uuid) is not supplied.
   uuid:
     description:
     - UUID of the instance to gather facts if known, this is VMware's unique identifier.
     - This is a required parameter, if parameter C(name) is not supplied.
   folder:
     description:
     - Destination folder, absolute or relative path to find an existing guest.
     - This is a required parameter, only if multiple VMs are found with same name.
     - The folder should include the datacenter. ESXi server's datacenter is ha-datacenter.
     - 'Examples:'
     - '   folder: /ha-datacenter/vm'
     - '   folder: ha-datacenter/vm'
     - '   folder: /datacenter1/vm'
     - '   folder: datacenter1/vm'
     - '   folder: /datacenter1/vm/folder1'
     - '   folder: datacenter1/vm/folder1'
     - '   folder: /folder1/datacenter1/vm'
     - '   folder: folder1/datacenter1/vm'
     - '   folder: /folder1/datacenter1/vm/folder2'
   cluster:
     description:
     - The name of cluster where the virtual machine will run.
     - This is a required parameter, if C(esxi_hostname) is not set.
     - C(esxi_hostname) and C(cluster) are mutually exclusive parameters.
     - This parameter is case sensitive.
   esxi_hostname:
     description:
     - The ESXi hostname where the virtual machine will run.
     - This is a required parameter, if C(cluster) is not set.
     - C(esxi_hostname) and C(cluster) are mutually exclusive parameters.
     - This parameter is case sensitive.
   datacenter:
     default: ha-datacenter
     description:
     - The datacenter name to which virtual machine belongs to.
     - This parameter is case sensitive.
   gather_network_facts:
     description:
     - If set to True, return settings of the network adapters, other attributes are ignored.
     - If set to False, will reconfigure network adapters according to the attributes.
     type: bool
     default: False
   networks:
     description:
     - A list of network adapters, not in the order of the NICs.
     - C(label) or C(device_type) is required to reconfigure an existing network adapter.
     - All parameters and VMware object names are case sensetive.
     - 'One of the below parameters is required per entry:'
     - ' - C(label) (string): the Network adapter label value, e.g., "Network Adapter 1". If not specified, will use
           the first matched one if there are more than one network adapters with the same C(device_type).'
     - ' - C(device_type) (string): Virtual network device:
           one of C(e1000), C(e1000e), C(pcnet32), C(vmxnet2), C(vmxnet3) (default), C(sriov).'
     - ' - C(name) (string): Name of the portgroup or distributed virtual portgroup for this interface.
           When specifying distributed virtual portgroup make sure given C(esxi_hostname) or C(cluster) is associated with it.'
     - ' - C(vlan) (integer): VLAN number for this interface.'
     - ' - C(state) (string): Specify the status of the target network adapter, if C(present), then will do reconfiguriton
           on it if exists, if C(new), then will add this new network adapter, if C(absent), then will remove this network adapter.'
     - 'Optional parameters per entry (used for virtual hardware):'
     - ' - C(mac) (string): Manual specified MAC address of this network adapter.'
     - ' - C(dvswitch_name) (string): Name of the distributed vSwitch.
           This value is required if multiple distributed portgroups exists with the same name.'
     - ' - C(connected) (bool): Indicates that virtual network adapter connects to the associated virtual machine.'
     - ' - C(start_connected) (bool): Indicates that virtual network adapter starts with associated virtual machine powers on.'
extends_documentation_fragment: vmware.documentation
'''

EXAMPLES = '''
- name: Change network adapter settings of virtual machine
  vmware_guest_network:
    hostname: "{{ vcenter_hostname }}"
    username: "{{ vcenter_username }}"
    password: "{{ vcenter_password }}"
    datacenter: "{{ datacenter_name }}"
    validate_certs: no
    name: test-vm
    gather_network_facts: false
    networks:
      - name: VM Network
        state: new
      - name: VM Network
        state: present
        device_type: e1000e
        mac: 00:50:56:68:52:23    
  delegate_to: localhost
  register: network_facts
'''

RETURN = """
network_data:
    description: metadata about the virtual machine's network adapter after managing them
    returned: always
    type: dict
    sample: {
        "0": {
            "label": "Network Adapter 1",
            "name": "VM Network",
            "mac_addr": "00:50:56:89:dc:05",
            "unit_number": 7,
            "wake_onlan": false,
            "allow_guest_ctl": true,
            "connected": true,
            "start_connected": true,
        },
    }
"""

import re

try:
    from pyVmomi import vim
except ImportError:
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils._text import to_native
from ansible.module_utils.vmware import PyVmomi, vmware_argument_spec, wait_for_task, find_obj, get_all_objs


class PyVmomiHelper(PyVmomi):
    def __init__(self, module):
        super(PyVmomi, self).__init__(module)
        self.change_detected = False
        self.config_spec = vim.vm.ConfigSpec()
        self.config_spec.deviceChange = []
        self.nic_device_type = dict(pcnet32=vim.vm.device.VirtualPCNet32(),
                        vmxnet2=vim.vm.device.VirtualVmxnet2(),
                        vmxnet3=vim.vm.device.VirtualVmxnet3(),
                        e1000=vim.vm.device.VirtualE1000(),
                        e1000e=vim.vm.device.VirtualE1000e(),
                        sriov=vim.vm.device.VirtualSriovEthernetCard(),
                        )

    def get_device_type(self, device_type=None):
        """ Get network adapter device type """
        if device_type and device_type in self.nic_device_type:
            return self.nic_device_type[device_type]
        else:
            self.module.fail_json(msg='Invalid network device_type %s' % device_type)

    def get_parent_datacenter(self, obj):
        """ Walk the parent tree to find the object's datacenter """
        datacenter = None
        while True:
            if not hasattr(obj, 'parent'):
                break
            obj = obj.parent
            if isinstance(obj, vim.Datacenter):
                datacenter = obj
                break
        return datacenter

    def get_network_by_name(self, content, name):
        """ Check if network with specified name exists """
        vimtype = [vim.Network]
        result = find_obj(content, vimtype, name)
        if result:
            if to_text(self.get_parent_datacenter(result).name) != to_text(self.params['datacenter']):
                objects = get_all_objs(content, vimtype)
                for obj in objects:
                    if to_text(self.get_parent_datacenter(obj).name) == to_text(self.params['datacenter']):
                        if to_text(obj.name) == to_text(name):
                            return True
            else:
                return True
        return False

    def get_network_devices_by_type(self, vm=None, device_type=None):
        """ Get network adapter list with the name type """
        nic_devices = []
        if vm is None:
            return nic_devices
        device_type_obj = self.get_device_type(device_type=device_type)
        for device in vm.config.hardware.device:
            if isinstance(device, device_type_obj):
                nic_devices.append(device)

        return nic_devices

    def get_network_device_by_label(self, vm=None, device_label=None):
        """ Get network adapter with the specified label """
        nic_device = None
        if vm is None or device_label is None:
            return nic_device
        for device in vm.config.hardware.device:
            for device_type in self.nic_device_type.keys():
                if isinstance(device, self.get_device_type(device_type=device_type)):
                    if device.deviceInfo.label == device_label:
                        nic_device = device
                        break

        return nic_device

    @staticmethod
    def is_valid_mac_addr(mac_addr):
        """ Validate MAC address for given string """
        mac_addr_regex = re.compile('[0-9a-f]{2}([-:])[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$')
        return bool(mac_addr_regex.match(mac_addr))

    def create_network_adapter(self, device_info):
        nic = vim.vm.device.VirtualDeviceSpec()
        nic.device = self.get_device_type(device_type=device_info.get('device_type', 'vmxnet3'))
        nic.device.deviceInfo = vim.Description()
        # nic.device.deviceInfo.label = device_info.get('label', None)
        nic.device.deviceInfo.summary = device_info['name']
        nic.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
        nic.device.connectable.startConnected = bool(device_info.get('start_connected', True))
        nic.device.connectable.allowGuestControl = True
        nic.device.connectable.connected = True
        if 'mac' in device_info:
            nic.device.addressType = 'manual'
            nic.device.macAddress = device_info['mac']
        else:
            nic.device.addressType = 'generated'

        return nic

    def get_network_facts(self, vm_obj):
        network_facts = dict()
        if vm_obj is None:
            return network_facts

        nic_index = 0
        for nic in vm_obj.config.hardware.device:
            if isinstance(nic, tuple(self.nic_device_type.values())):
                network_facts[nic_index] = dict(
                    label=nic.deviceInfo.label,
                    name=nic.deviceInfo.summary,
                    mac_addr=nic.macAddress,
                    unit_number=nic.unitNumber,
                    wake_onlan=nic.wakeOnLanEnabled,
                    allow_guest_ctl=nic.connectable.allowGuestControl,
                    connected=nic.connectable.connected,
                    start_connected=nic.connectable.startConnected,
                )
                nic_index += 1

        return network_facts

    def sanitize_network_params(self):
        network_list = []
        valid_state = ['new', 'present', 'absent']
        if len(self.params['networks']) != 0:
            for network in self.params['networks']:
                if 'state' not in network or network['state'].lower() not in valid_state:
                    self.module.fail_json(msg="Network adapter state not specified or invalid %s, valid values: "
                                              "%s" % (network.get('state', ''), valid_state))

                if 'name' not in network and 'vlan' not in network:
                    self.module.fail_json(msg="Please specify at least network name or VLAN name for VM network config.")

                if 'name' in network and not self.get_network_by_name(self.content, network['name']):
                    self.module.fail_json(msg="Network '%(name)s' does not exist." % network)
                elif 'vlan' in network:
                    objects = get_all_objs(self.content, [vim.dvs.DistributedVirtualPortgroup])
                    dvps = [x for x in objects if to_text(self.get_parent_datacenter(x).name) == to_text(self.params['datacenter'])]
                    for dvp in dvps:
                        if hasattr(dvp.config.defaultPortConfig, 'vlan') and \
                                isinstance(dvp.config.defaultPortConfig.vlan.vlanId, int) and \
                                        str(dvp.config.defaultPortConfig.vlan.vlanId) == str(network['vlan']):
                            network['name'] = dvp.config.name
                            break
                        if 'dvswitch_name' in network and \
                                        dvp.config.distributedVirtualSwitch.name == network['dvswitch_name'] and \
                                        dvp.config.name == network['vlan']:
                            network['name'] = dvp.config.name
                            break
                        if dvp.config.name == network['vlan']:
                            network['name'] = dvp.config.name
                            break
                    else:
                        self.module.fail_json(msg="VLAN '%(vlan)s' does not exist." % network)

                if 'device_type' in network and network['device_type'] not in self.nic_device_type.keys():
                    self.module.fail_json(msg="Device type specified '%s' is invalid. "
                                              "Valid types %s " % (network['device_type'], self.nic_device_type.keys()))

                if 'mac' in network and not self.is_valid_mac_addr(network['mac']):
                    self.module.fail_json(msg="Device MAC address '%s' is invalid. "
                                              "Please provide correct MAC address." % network['mac'])

                network_list.append(network)

        return network_list

    def get_network_config_spec(self, vm_obj):
        network_list = self.sanitize_network_params()
        if len(network_list) == 0:
            return
        else:
            for network in network_list:
                # add new network adapter
                if network['state'].lower() == 'new':
                    nic_spec = self.create_network_adapter(network)
                    nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
                    self.change_detected = True
                # re-configure network adapter or remove network adapter
                else:
                    nic_device = None
                    if 'label' in network:
                        nic_device = self.get_network_device_by_label(vm_obj, device_label=network['label'])
                    elif 'label' not in network and 'device_type' in network:
                        nic_devices = self.get_network_devices_by_type(vm_obj, device_type=network['device_type'])
                        if len(nic_devices) != 0:
                            nic_device = nic_devices[0]
                    else:
                        self.module.fail_json(msg="Should specify 'label' or 'device_type' parameter to re-configure network adapter")
                    if nic_device is not None:
                        nic_spec = vim.vm.device.VirtualDeviceSpec()
                        if network['state'].lower() == 'present':
                            if 'start_connected' in network and nic_device.connectable.startConnected != network['start_connected']:
                                nic_device.connectable.startConnected = network['start_connected']
                                self.change_detected = True
                            if 'connected' in network and nic_device.connectable.connected != network['connected']:
                                nic_device.connectable.connected = network['connected']
                                self.change_detected = True
                            if nic_device.deviceInfo.summary != network['name']:
                                nic_device.deviceInfo.summary = network['name']
                                self.change_detected = True
                            if 'mac' in network and nic_device.macAddress != network['mac']:
                                if vm_obj.runtime.powerState != vim.VirtualMachinePowerState.poweredOff:
                                    self.module.fail_json(msg='Expected power state is poweredOff to re-configure MAC address')
                                nic_device.addressType = 'manual'
                                nic_device.macAddress = network['mac']
                                self.change_detected = True
                            nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
                            nic_spec.device = nic_device
                        elif network['state'].lower() == 'absent':
                            nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
                            nic_spec.device = nic_device
                            self.change_detected = True
                if self.change_detected:
                    self.configspec.deviceChange.append(nic_spec)

    def reconfigure_vm_network(self, vm_obj):
        results = {'changed': False, 'failed': False, 'network_data': None}
        if self.params['gather_network_facts'] is not None and self.params['gather_network_facts']:
            results['network_data'] = self.get_network_facts(vm_obj)
            return results
        else:
            self.get_network_config_spec(vm_obj)
            try:
                task = vm_obj.ReconfigVM_Task(spec=self.config_spec)
                wait_for_task(task)
            except vim.fault.InvalidDeviceSpec as invalid_device_spec:
                self.module.fail_json(msg="Failed to configure network adapter on given virtual machine due to invalid"
                                          " device spec : %s" % (to_native(invalid_device_spec.msg)),
                                      details="Please check ESXi server logs for more details.")
            except vim.fault.RestrictedVersion as e:
                self.module.fail_json(msg="Failed to reconfigure virtual machine due to"
                                          " product versioning restrictions: %s" % to_native(e.msg))
            if task.info.state == 'error':
                return {'changed': self.change_detected, 'failed': True, 'msg': task.info.error.msg}
            network_facts = self.get_network_facts(vm_obj)
            return {'changed': self.change_detected, 'failed': False, 'network_data': network_facts}


def main():
    argument_spec = vmware_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        uuid=dict(type='str'),
        folder=dict(type='str'),
        datacenter=dict(type='str', default='ha-datacenter'),
        esxi_hostname=dict(type='str'),
        cluster=dict(type='str'),
        gather_network_facts=dict(type='bool', default=False),
        networks=dict(type=list, default=[])
    )

    module = AnsibleModule(argument_spec=argument_spec, required_one_of=[['name', 'uuid']])
    pyv = PyVmomiHelper(module)
    vm = pyv.get_vm()
    if not vm:
        module.fail_json(msg='Unable to find the specified virtual machine %s' % (module.params.get('uuid')) or (module.params.get('name')))

    result = pyv.reconfigure_vm_network(vm_obj=vm)
    if result['failed']:
        module.fail_json(**result)
    else:
        module.exit_json(**result)


if __name__ == '__main__':
    main()