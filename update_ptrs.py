from extras.scripts import Script
from dcim.models import Device, Interface, VirtualLink
from ipam.models import IPAddress
from ipam.models import L2VPNTermination
from ipam.choices import IPAddressRoleChoices

class UpdatePTRs(Script):

    class Meta:
        name = "Regularize PTR Fields"
        description = "Updates IP address DNS fields with standardized PTR names"


    def run(self, data, commit):
        output = ""

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
                    out = f' UPDATED   {str(address.address):30} {address.dns_name}'
                else:
                    out = f'           {str(address.address):30} {address.dns_name}'

                output += f'{out}\n'

        self.log_success('DNS (PTR) regularization complete.')
        return f'IP address PTR records (dns) updated:\n-----\n{output}'
