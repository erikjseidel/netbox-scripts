import netaddr
from copy import copy
from extras.scripts import *
from extras.models import Tag
from dcim.models import Interface, VirtualLink
from ipam.models import Prefix, IPAddress
from utilities.exceptions import AbortScript

class GenerateNew(Script):

    class Meta:
        name = "Generate new IP Addresses"
        description = "Generate new IP addresses for PTP interfaces tagged with 'renumber' tag"
        scheduling_enabled = False
        commit_default = False

    ipv4_prefix = ObjectVar(
        description="Target IPv4 Prefix",
        model=Prefix,
        query_params={
            'family': 4
        }
    )

    ipv6_prefix = ObjectVar(
        description="Target IPv6 Prefix",
        model=Prefix,
        query_params={
            'family': 6
        }
    )

    def run(self, data, commit):
        updated = False
        output = ""

        renumber = Tag.objects.get(name='renumber')
        l2ptp = Tag.objects.get(name='l2ptp')
        l3ptp = Tag.objects.get(name='l3ptp')

        ips4 = data['ipv4_prefix'].get_available_ips()
        ips6 = data['ipv6_prefix'].get_available_ips()

        # Load or create 'new_ip' tag which is used to mark IPs created by this method.
        if Tag.objects.filter(name='new_ip'):
            new_ip = Tag.objects.get(name='new_ip')
        else:
            new_ip = Tag( 
                    name='new_ip',
                    slug='new_ip',
                    description='New IP assignment generated be IP Generator',
                    )
            new_ip.save()

        # Load or create 'prune' tag which is used to mark existing IPs assigned to
        # interfaces assigned new IPs by this method.
        if Tag.objects.filter(name='prune'):
            prune = Tag.objects.get(name='prune')
        else:
            prune = Tag( 
                    name='prune',
                    slug='prune',
                    description='IP assignment pending removal by IP Generator',
                    )
            prune.save()

        scanned_ids = []
        for interface in Interface.objects.filter(tags=renumber):
            # Track already processed interfaces
            scanned_ids.append(interface.id)

            if l2ptp in interface.tags.all():
                new_tag = l2ptp
            elif l3ptp in interface.tags.all():
                new_tag = l3ptp
            else:
                # only interfaces tagged as ptp can be renumbered.
                continue

            if not ( vl := interface.virtual_link ):
                continue

            if vl.interface_a.id == interface.id:
                peer_iface = Interface.objects.get(id=vl.interface_b.id)
            else:
                peer_iface = Interface.objects.get(id=vl.interface_a.id)

            if peer_iface.id in scanned_ids:
                # We already scanned this interface. Skip it.
                continue

            # Select IPv4 /31 and create IP objects
            for cidr4 in ips4.iter_cidrs():
                if cidr4.prefixlen < 32:
                    break
            prefix4 = copy(cidr4)
            prefix4.prefixlen = 31
            ips4.remove(prefix4)

            # Select IPv6 /127 and create IP objects
            for cidr6 in ips6.iter_cidrs():
                if cidr6.prefixlen < 128:
                    break
            prefix6 = copy(cidr6)
            prefix6.prefixlen = 127
            ips6.remove(prefix6)

            output += f'{interface.name}<->{peer_iface.name}:\n' 
            output += f'\t{new_tag.name}: {prefix4[0]} {prefix4[1]} {prefix6[0]} {prefix6[1]}\n'

            # Tag the old IP address for removal.
            for i in [interface, peer_iface]: 
                for ip in IPAddress.objects.filter(interface=i):
                    ip.tags.add(prune)
                    output += f'\t{prune.name}: {ip.address}\n'
                self.log_info(f'Existing IP addresses on {i.device.name}:{i.name} marked for removal')

            # Create IP assignments for interface and peer_iface.
            def create_ips(iface, addrs):
                for addr in addrs:
                    ip = IPAddress(
                            address = addr,
                            assigned_object = iface,
                            )
                    ip.save()
                    ip.tags.add(new_tag, new_ip)
                    iface.tags.remove(renumber)
                self.log_info(f'New IP addresses created for {iface.device.name}:{iface.name}')

            create_ips(interface,  [prefix4[0], prefix6[0]])
            create_ips(peer_iface, [prefix4[1], prefix6[1]])
            updated = True


        if updated:
            self.log_success('New IP address generation for selected PTP interfaces complete.')
        else:
            self.log_failure('No eligible interfaces marked for renumbering.')
        return output


class PruneIPs(Script):

    class Meta:
        name = "Clean up IP addresses"
        description = "Remove IPs marked for removal ('prune' tag) and clear 'new' tags"
        scheduling_enabled = False
        commit_default = False

    def run(self, data, commit):
        prune = Tag.objects.get(name='prune')
        new_ip = Tag.objects.get(name='new_ip')

        output = "Deleted:\n-----\n"
        for ip in IPAddress.objects.filter(tags=prune):
            ip.delete()
            self.log_info(f'{ip.address} deleted')
            output += f'{ip.address}\n'
        self.log_success('Old IP address deletion complete.')

        output += "\nCleared new IP tag:\n-----\n"
        for ip in IPAddress.objects.filter(tags=new_ip):
            ip.tags.remove(new_ip)
            self.log_info(f'{ip.address} \'new\' tag removed')
            output += f'{ip.address}\n'
        self.log_success('New IP address cleanup complete.')

        return output
