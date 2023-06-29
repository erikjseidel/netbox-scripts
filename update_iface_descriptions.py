import yaml
from extras.scripts import Script
from dcim.models import Device, Interface, VirtualLink
from ipam.models import L2VPNTermination
from scripts.util.yaml_out import yaml_out

class UpdateIfaceDescriptions(Script):

    class Meta:
        name = "Regularize Interface Descriptions"
        description = "Updates interface description fields with standardized descriptions"


    @yaml_out
    def run(self, data, commit):
        out = {}

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

            if updated:
                interface.save()
                self.log_info(f'Updated {interface.device.name}:{interface.name} description to {interface.description}')
                if interface.device.name not in out:
                    out[interface.device.name] = {}
                out[interface.device.name][interface.name] = {
                        'description' : interface.description,
                        'action'      : 'updated',
                        }

        if out:
            msg = 'Interface description regularization complete.'
            self.log_success(msg)
            ret =  { 'result': True, 'out': out, 'comment': msg }
        else:
            msg = 'All interfaces up-to-date. No changes made.'
            self.log_warning(msg)
            ret =  { 'result': True, 'comment': msg }

        return ret
