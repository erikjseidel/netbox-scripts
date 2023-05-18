from extras.scripts import Script
from dcim.models import Device, Interface, VirtualLink
from ipam.models import L2VPNTermination

class MyScript(Script):

    class Meta:
        name = "Regularize Interface Descriptions"
        scheduling_enabled = False


    def run(self, data, commit):
        output = ""

        for interface in Interface.objects.all():
            updated = False

            description = None
            if interface.name.startswith('dum'):
                num = ''.join(x for x in interface.name if x.isdigit())

                if int(num) in range(10, 200):
                    description = f'[T=lo][internal] {interface.device.name} Internal Loopback'
                elif int(num) in range(200, 256):
                    description = f'[T=lo][public] {interface.device.name} Public Loopback'

            elif vl := interface.virtual_link:

                if vl.interface_a.id == interface.id:
                    peer_iface = Interface.objects.get(id=vl.interface_b.id)
                else:
                    peer_iface = Interface.objects.get(id=vl.interface_a.id)

                if peer_iface:
                    my_name = interface.device.name
                    peer_name = peer_iface.device.name

                    description = f'[T=ptp][{my_name}:{interface.name}:{peer_name}:{peer_iface.name}]'

            elif link_peers := interface.link_peers:
                assert len(link_peers) == 1

                cid = link_peers[0].circuit.cid
                provider = link_peers[0].circuit.provider.name
                description = f'[T=transit][{provider}:{cid}]'

            elif l2vpn := interface.l2vpn_termination:
                name = l2vpn.l2vpn.name
                slug = l2vpn.l2vpn.slug
                description = f'[T=transit][{slug}] {name}'

            elif vlan := interface.untagged_vlan:
                description = f'[T=lan][vid={vlan.vid}] {vlan.name}'

            if description and interface.description != description:
                interface.description = description
                updated = True

            path = f'{interface.device.name}:{interface.name}'

            if updated:
                interface.save()
                result = 'UPDATED'
                out = f'{result:10} {path:18} {interface.description}'
            else:
                result = ''
                out = f'{result:10} {path:18} {interface.description}'

            output += f'{out}\n'

        return f'Interface descriptions updated:\n-----\n{output}'
