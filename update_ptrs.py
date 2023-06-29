from extras.scripts import Script
from dcim.models import Device, Interface, VirtualLink
from ipam.models import IPAddress
from ipam.models import L2VPNTermination
from ipam.choices import IPAddressRoleChoices
from scripts.util.yaml_out import yaml_out

class UpdatePTRs(Script):

    class Meta:
        name = "Regularize PTR Fields"
        description = "Updates IP address DNS fields with standardized PTR names"


    @yaml_out
    def run(self, data, commit):
        out = {}

        for address in IPAddress.objects.all():
            ptr_prefix = None

            tags = address.tags.names()
            interface = address.interface.get()

            if 'l2ptp' in tags or 'l3ptp' in tags:
                if vl := interface.virtual_link:
                    if vl.interface_a.id == interface.id:
                        peer_iface = Interface.objects.get(id=vl.interface_b.id)
                    else:
                        peer_iface = Interface.objects.get(id=vl.interface_a.id)

                    if peer_iface:
                        ptr_prefix = '%s-%s-%s-%s.ptp' % (  interface.device.name.lower(),
                                                            interface.name.lower(),
                                                            peer_iface.device.name.lower(),
                                                            peer_iface.name.lower()         )
    
                if not ptr_prefix:
                    ptr_prefix = '%s-%s' % ( interface.device.name.lower(), interface.name.lower() )

            if 'lan' in tags:
                if vlan := interface.untagged_vlan:
                    ptr_prefix = '%s-vlan%s-gw' % ( interface.device.name.lower(), str(vlan.vid) )
                else:
                    ptr_prefix = '%s-lan-gw' % interface.device.name.lower()

            elif address.role == IPAddressRoleChoices.ROLE_LOOPBACK:
                ptr_prefix = '%s.loopbacks' % interface.device.name.lower()

            if ptr_prefix and not address.address.is_private():
                dns_name = ptr_prefix + '.as36198.net'

                if address.dns_name != dns_name:
                    address.dns_name = dns_name
                    address.save()
                    self.log_info(f'Updated {address.address} PTR to {address.dns_name}')
                    out[str(address.address)] = {
                            'ptr'    : address.dns_name,
                            'action' : 'updated',
                            }

        if out:
            msg = 'DNS (PTR) regularization complete.'
            self.log_success(msg)
            ret =  { 'result': True, 'out': out, 'comment': msg }
        else:
            msg = 'All DNS (PTR) up-to-date. No changes made.'
            self.log_warning(msg)
            ret =  { 'result': True, 'comment': msg }

        return ret
