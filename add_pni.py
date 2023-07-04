import netaddr
from extras.scripts import Script, ObjectVar, MultiObjectVar, IntegerVar, BooleanVar, StringVar, IPAddressWithMaskVar
from extras.models import Tag
from django.core.exceptions import ObjectDoesNotExist
from dcim.models import Device, Interface
from dcim.choices import InterfaceTypeChoices, InterfaceModeChoices
from ipam.models import Prefix, IPAddress, VLAN, Role
from ipam.choices import IPAddressStatusChoices
from scripts.util.cancel_script import CancelScript, cancellable
from scripts.util.yaml_out import yaml_out

# Tag Names
LacpL34Tag = 'layer3+4'
LacpSlowTag = 'lacp_slow'

class CreateBundle(Script):

    class Meta:
        name = "Create Bundle"
        description = "Create a new LACP bundle from eligible ethernet interfaces" 
        scheduling_enabled = False
        commit_default = False

    device = ObjectVar(
            label = "Device",
            description="Target Device",
            model=Device,
            )

    interfaces = MultiObjectVar(
            label = "Interfaces",
            description="Target Interfaces",
            model=Interface,
            query_params={
                'device_id': '$device',
                'kind' : 'physical',
                },
            )

    layer3_4 = BooleanVar(
            label="Layer 3+4",
            description='Use layer 3+4 hashing (non-standard)'
            )

    lacp_slow = BooleanVar(
            label="LACP slow",
            description='Use LACP "slow" rate instead of fast'
            )

    @yaml_out
    @cancellable
    def run(self, data, commit):

        device = data['device']
        interfaces = data['interfaces']
        layer3_4 = data['layer3_4']
        lacp_slow = data['lacp_slow']

        entry = {}

        for i in range(0,101):
            # Find first available Vyos bundle name
            lacp_name = f'bond{i}'
            if not Interface.objects.filter(device=device, name=lacp_name):
                break

        lacp = Interface(
                name=lacp_name,
                type=InterfaceTypeChoices.TYPE_LAG,
                device=device,
                )

        lacp.save()

        entry_tags = []
        if layer3_4:
            lacp.tags.add( Tag.objects.get(name=LacpL34Tag) )
            entry_tags.append(LacpL34Tag)

        if lacp_slow:
            lacp.tags.add( Tag.objects.get(name=LacpSlowTag) )
            entry_tags.append(LacpSlowTag)

        if entry_tags:
            entry['tags'] = entry_tags

        entry['status'] = 'created'
        self.log_info(f'LACP interface {lacp.name} created')

        int_names = []
        for interface in interfaces:
            if interface.count_ipaddresses > 0:
                raise CancelScript(f'Interface {interface.name} already has IPs assigned to it')

            if interface.lag:
                raise CancelScript(f'Interface {interface.name} already assigned to a LAG')

            description = (interface.description or "")

            interface.lag = lacp
            interface.description = f'{lacp.name}: {description}'
            interface.save()

            self.log_debug(f'interface {interface.name} assigned to {lacp_name}')
            int_names.append(interface.name)

        entry['interfaces'] = int_names

        out = {}
        out[device.name] = {}
        out[device.name][lacp.name] = entry

        msg = 'Dry run. Database changes rolled back'
        if commit:
            msg = 'Changes committed'

        return {
            'comment' : msg,
            'result'  : commit,
            'out'     : out,
            }


class AddPNI(Script):

    class Meta:
        name = "Add PNI"
        description = "Create a new PNI interface and IP addresses" 
        scheduling_enabled = False
        commit_default = False

        fieldsets = (
            ('Interface Information', ('device', 'interface', 'vlan_id', 'circuit_id')),
            ('Peer Information', ('peer_asn', 'peer_name')),
            ('IP Assignments', ('autogen_ips', 'my_ipv4', 'my_ipv6')),
        )

    device = ObjectVar(
            label = "Device",
            description="Target Device",
            model=Device,
            )

    interface = ObjectVar(
            label = "Interface",
            description="Target Interface",
            model=Interface,
            query_params={
                'device_id': '$device'
                },
            )

    vlan_id = IntegerVar(
            label = 'VLAN ID',
            description='Leave blank for no VLAN tagging',
            min_value = 1,
            max_value = 4097,
            required=False,
            )

    peer_asn = IntegerVar(
            label = 'Peer ASN',
            description='Used to generate description',
            min_value = 1,
            max_value = 4294967295,
            required=False,
            )

    peer_name = StringVar(
            label = 'Peer Name',
            description='Used to generate description',
            required=False,
            )

    circuit_id = StringVar(
            label = 'Circuit ID',
            description='Used to generate desccription in non-LACP configurations',
            required=False,
            )

    autogen_ips = BooleanVar(
            label="Autogenerate IPs",
            description='Select to automatically generate IP addresses'
            )

    my_ipv4 = IPAddressWithMaskVar(
            label="Local IPv4",
            description='Required if Autogenerate IPs not selected',
            required=False,
            )

    my_ipv6 = IPAddressWithMaskVar(
            label="Local IPv6",
            description='Required if Autogenerate IPs not selected',
            required=False,
            )

    @yaml_out
    @cancellable
    def run(self, data, commit):

        if isinstance(data['device'], str) and isinstance(data['interface'], str):
            # API call using strings to identify the interface
            try:
                device = Device.objects.get(name=data['device'])
                interface = Interface.objects.get(device=device, name=data['interface'])
            except ObjectDoesNotExist:
                msg = f"Interface {data['device']}:{data['interface']} not found or not unique"
                raise CancelScript(msg)

            if my_ipv4 := data.get('my_ipv4'):
                my_ipv4 = netaddr.IPNetwork(my_ipv4)

            if my_ipv6 := data.get('my_ipv6'):
                my_ipv6 = netaddr.IPNetwork(my_ipv6)

        else:
            my_ipv4 = data.get('my_ipv4')
            my_ipv6 = data.get('my_ipv6')
            interface = data['interface']

        device = interface.device
        site = device.site

        peer_asn = ( str(data['peer_asn']) or "" )
        peer_name = ( data['peer_name'] or "" )
        circuit_id = ( data['circuit_id'] or "" )

        ipam_role = Role.objects.get(slug='pni-autogeneration-role')

        entry = {}

        if vlan_id := data.get('vlan_id'):

            if vlan_id not in range(1,4095):
                msg = f'vlan_id must be an integer between 1 and 4094'
                raise CancelScript(msg)

            if interface.mode == InterfaceModeChoices.MODE_ACCESS:
                # We do not support Q-in-Q; so if already access mode then exit.
                raise CancelScript(f'Cannot assign a VLAN to interface {interface.name}')

            vlan_name = f'{interface.name}.{int(vlan_id)}'

            if found_vlan := Interface.objects.filter(name=vlan_name).first():
                interface = found_vlan
            else:
                if not ( nb_vlan := VLAN.objects.filter(vid=vlan_id, site=site).first() ):
                    nb_vlan = VLAN(
                            site = site,
                            vid  = vlan_id,
                            role=ipam_role,
                            )
                    nb_vlan.save()
                    self.log_info(f'VLAN {nb_vlan.vid} at {site.name} created')
                else:
                    self.log_info(f'VLAN {nb_vlan.vid} at {site.name} found')

                vlan = Interface(
                        name=vlan_name,
                        type=InterfaceTypeChoices.TYPE_VLAN,
                        mode=InterfaceModeChoices.MODE_ACCESS,
                        parent=interface,
                        device=device,
                        untagged_vlan=nb_vlan,
                        )

                vlan.save()
                entry.update({
                        'status'  : 'created',
                        })
                self.log_info(f'VLAN interface {vlan.name} created')

                interface=vlan

            entry['vlan'] = {
                    'vid'     : interface.untagged_vlan.vid,
                    'site'    : interface.untagged_vlan.site.name,
                    }

        if interface.count_ipaddresses > 0:
            msg = f'Interface {interface.name} already has IPs assigned to it'
            raise CancelScript(msg)

        description = f'[peer={peer_asn}][{peer_name}]'
        if interface.type != InterfaceTypeChoices.TYPE_LAG:
            description += f'[CID: {circuit_id}]'

        interface.description = description
        interface.save()

        entry['description'] = interface.description

        # We assume that PNIs are Layer2 PtP
        interface.tags.add( Tag.objects.get(name='l2ptp') )
        entry['tags'] = list(interface.tags.names())

        if data.get('autogen_ips'):
            autogen_prefix_v4 = None
            autogen_prefix_v6 = None

            for prefix in Prefix.objects.filter(role=ipam_role):
                # Still need to check for available IPs here
                if not autogen_prefix_v4 and prefix.family == 4:
                    autogen_prefix_v4 = prefix
                if not autogen_prefix_v6 and prefix.family == 6:
                    autogen_prefix_v6 = prefix

            if not (autogen_prefix_v4 and autogen_prefix_v6):
                msg = f'Autogen v4 or v6 prefix not found'
                raise CancelScript(msg)

            for cidr4 in autogen_prefix_v4.get_available_ips().iter_cidrs():
                if cidr4.prefixlen < 32:
                    break
            my_ipv4 = netaddr.IPNetwork(f'{cidr4[0]}/31')

            for cidr6 in autogen_prefix_v6.get_available_ips().iter_cidrs():
                if cidr6.prefixlen < 128:
                    break
            my_ipv6 = netaddr.IPNetwork(f'{cidr6[0]}/127')

        elif my_ipv4 and my_ipv6:
            if not ( my_ipv4.version == 4 and my_ipv6.version == 6 ):
                msg = f'Invalid family for one or both IP assignments'
                raise CancelScript(msg)

        else:
            msg = f'Either autogen must be selected or IP fields must be completed'
            raise CancelScript(msg)

        entry_addr = []
        for addr in [my_ipv4, my_ipv6]:
            for a in IPAddress.objects.filter(address=addr):
                if a.assigned_object:
                    msg = f'{addr} is already assigned'
                    raise CancelScript(msg)

            nb_ip = IPAddress(
                    address = addr,
                    assigned_object = interface,
                    status = IPAddressStatusChoices.STATUS_ACTIVE
                    )

            nb_ip.full_clean()
            nb_ip.save()
            entry_addr.append(str(nb_ip.address))
            self.log_info(f'{nb_ip.address} created and assigned to {interface}')

        entry['address'] = entry_addr

        out = {}
        out[device.name] = {}
        out[device.name][interface.name] = entry

        msg = 'Dry run. Database changes rolled back'
        if commit:
            msg = 'Changes committed'

        return {
            'comment' : msg,
            'result'  : commit,
            'out'     : out,
            }
