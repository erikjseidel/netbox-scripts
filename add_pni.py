import netaddr
from extras.scripts import Script
from extras.scripts import *
from extras.models import Tag
from dcim.models import Device, Interface
from dcim.choices import InterfaceTypeChoices, InterfaceModeChoices
from ipam.models import Prefix, IPAddress, VLAN, Role
from ipam.choices import IPAddressStatusChoices
from utilities.exceptions import AbortScript

class AddPNI(Script):

    class Meta:
        name = "Add PNI"
        scheduling_enabled = False
        commit_default = False

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

    def run(self, data, commit):
        interface = data['interface']
        device = interface.device
        site = device.site

        ipam_role = Role.objects.get(slug='pni-autogeneration-role')

        if vlan_id := data['vlan_id']:
            if interface.mode == InterfaceModeChoices.MODE_ACCESS:
                # We do not support Q-in-Q; so if already access mode then exit.
                raise AbortScript(f'Cannot assign a VLAN to interface {interface.name}')

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

                vlan = Interface(
                        name=vlan_name,
                        type=InterfaceTypeChoices.TYPE_VLAN,
                        mode=InterfaceModeChoices.MODE_ACCESS,
                        parent=interface,
                        device=device,
                        untagged_vlan=nb_vlan,
                        )

                vlan.save()
                self.log_info(f'VLAN interface {vlan.name} created')

                interface=vlan

        if interface.count_ipaddresses > 0:
            raise AbortScript(f'Interface {interface.name} already has IPs assigned to it')

        if data['autogen_ips']:
            autogen_prefix_v4 = None
            autogen_prefix_v6 = None

            for prefix in Prefix.objects.filter(role=ipam_role):
                # Still need to check for available IPs here
                if not autogen_prefix_v4 and prefix.family == 4:
                    autogen_prefix_v4 = prefix
                if not autogen_prefix_v6 and prefix.family == 6:
                    autogen_prefix_v6 = prefix

            if not (autogen_prefix_v4 and autogen_prefix_v6):
                raise AbortScript(f'Autogen v4 or v6 prefix not found')

            for cidr4 in autogen_prefix_v4.get_available_ips().iter_cidrs():
                if cidr4.prefixlen < 32:
                    break
            my_ipv4 = netaddr.IPNetwork(f'{cidr4[0]}/31')

            for cidr6 in autogen_prefix_v6.get_available_ips().iter_cidrs():
                if cidr6.prefixlen < 128:
                    break
            my_ipv6 = netaddr.IPNetwork(f'{cidr6[0]}/127')

        elif (my_ipv4 := data.get('my_ipv4')) and (my_ipv6 := data.get('my_ipv6')):
            if not ( my_ipv4.version == 4 and my_ipv6.version == 6 ):
                raise AbortScript(f'Invalid family for one or both IP assignments')

        else:
            raise AbortScript(f'Either autogen must be selected or IP fields must be completed')

        self.log_info(f'{my_ipv4} {my_ipv6}')

        for addr in [my_ipv4, my_ipv6]:
            if a := IPAddress.objects.filter(address=addr).first() and a.assigned_object:
                raise AbortScript(f'{addr} is already assigned')

            nb_ip = IPAddress(
                    address = addr,
                    assigned_object = interface,
                    status = IPAddressStatusChoices.STATUS_ACTIVE
                    )

            nb_ip.full_clean()
            nb_ip.save()
            self.log_info(f'{nb_ip.address} created and assigned to {interface}')
