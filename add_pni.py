import netaddr
from extras.scripts import Script, ObjectVar, MultiObjectVar, IntegerVar, BooleanVar, StringVar, IPAddressWithMaskVar
from extras.models import Tag
from django.utils.text import slugify
from django.core.exceptions import ObjectDoesNotExist
from circuits.models import Circuit, CircuitType, CircuitTermination, Provider, ProviderNetwork
from dcim.models import Device, Interface, Cable
from dcim.choices import InterfaceTypeChoices, InterfaceModeChoices
from ipam.models import Prefix, IPAddress, VLAN, Role
from ipam.choices import IPAddressStatusChoices
from scripts.util.cancel_script import CancelScript, cancellable
from scripts.util.yaml_out import yaml_out
from scripts.util.tags import lazy_load_tag, make_semantic_tag

# Tag Names
LacpL34Tag = 'layer3+4'
LacpSlowTag = 'lacp_slow'
L2ptpTag = 'l2ptp'
PNICircuitTypeSlug = 'private-network-interconnect'

# PNI identifiers
PNICircuitTag = 'pni:circuit'        # Ports or bundles created or managed by this module
PNIConfiguredTag = 'pni:configured'  # Interfaces configured by ConfigurePNI script

# Semantic tag prefixes
VlanVcidTagSuffix = 'pni:vcid'       # "Virtual" Circuit Identifiers for vPNI
PeerASNTagSuffix = 'pni:asn'         # Peer ASN for configured PNIs

class CreatePNI(Script):

    class Meta:
        name = "Create PNI"
        description = "Provision a new PNI circuit attached to an existing interface" 
        scheduling_enabled = False
        commit_default = False

        fieldsets = (
            ('Interface Information', ('device', 'interface')),
            ('Circuit Information', ('provider', 'peer_name', 'circuit_id')),
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
                'device_id': '$device',
                'kind' : 'physical',
                },
            )

    provider = ObjectVar(
            label = 'Peer (Provider)',
            description='Circuit peer (netbox provider) name. If blank, Peer Name will be used',
            model=Provider,
            required=False,
            )

    peer_name = StringVar(
            label = 'Peer (Provider) Name',
            description='Name of PNI peer (netbox provider)',
            required=False,
            )

    circuit_id = StringVar(
            label = 'Circuit ID',
            description='Circuit ID',
            )

    @yaml_out
    @cancellable
    def run(self, data, commit):

        device = data['device']
        interface = data['interface']
        provider = data.get('provider')
        peer_name = data.get('peer_name')
        circuit_id = data['circuit_id']

        circuit_type = CircuitType.objects.get(slug=PNICircuitTypeSlug)
        pni_tag = lazy_load_tag(PNICircuitTag)

        entry = {}

        if interface.link_peers:
            raise CancelScript(f'Interface {interface.name} is already wired')

        entry['provider'] = { 'action' : 'found' }
        if not provider:
            if peer_name:
                if not ( provider := Provider.objects.filter(name=peer_name).first() ):
                    provider = Provider(name=peer_name, slug=slugify(peer_name))
                    provider.save()
                    self.log_info(f'New provider {provider.name} created')
                    entry['provider']['action'] = 'created'

            else:
                raise CancelScript(f'Peer (Provider) not selected')

        entry['provider']['name'] = provider.name

        provider_network_name = f'{provider.name} Network'

        entry['provider']['network'] = {
                'action' : 'found',
                'name'   : provider_network_name,
                }

        if not ( provider_network := ProviderNetwork.objects.filter(provider=provider, name=provider_network_name).first() ):
            provider_network = ProviderNetwork(
                    provider = provider,
                    name = provider_network_name,
                    )
            provider_network.save()
            self.log_info(f'New provider network {provider_network.name} created')
            entry['provider']['network']['action'] = 'created'

        if Circuit.objects.filter(cid=circuit_id):
            raise CancelScript(f'Circuit {circuit_id} already exists')

        my_circuit = Circuit(
                cid = circuit_id,
                type = circuit_type,
                provider = provider,
                )
        my_circuit.save()
        self.log_info(f'New circuit {my_circuit.cid} created')

        entry['circuit'] = {
                'action' : 'created',
                'cid'    : my_circuit.cid,
                }

        interface.tags.add(pni_tag)

        interface.description = f'[RESERVED][{provider.name}][CID: {my_circuit.cid}]'
        interface.save()

        a_termination = CircuitTermination(
                circuit = my_circuit,
                term_side = 'A',
                site = device.site,
                )
        a_termination.save()
        self.log_info(f'New circuit A termination created')

        z_termination = CircuitTermination(
                circuit = my_circuit,
                term_side = 'Z',
                provider_network = provider_network,
                )
        z_termination.save()
        self.log_info(f'New circuit Z termination created')

        cable = Cable(
                a_terminations =  [ interface ],
                b_terminations =  [ a_termination ],
                )
        cable.save()
        self.log_info(f'Circuit {my_circuit.cid} and interface {interface.name} connected')

        entry['cable'] = {
                'action'        : 'created',
                'a_termination' : interface.name,
                'b_termination' : my_circuit.cid,
                }

        entry['interface'] = {
                'name'        : interface.name,
                'description' : interface.description,
                'tags'        : list(interface.tags.names()),
                }

        msg = 'Dry run. Database changes rolled back'
        if commit:
            msg = 'Changes committed'

        return {
            'comment' : msg,
            'result'  : commit,
            'out'     : entry,
            }


class CreateBundle(Script):

    class Meta:
        name = "Create Bundle"
        description = "Create a new LACP bundle from circuits created with Create PNI script" 
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
                'tag' : slugify(PNICircuitTag),
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

        circuit_type = CircuitType.objects.get(slug=PNICircuitTypeSlug)
        pni_tag = lazy_load_tag(PNICircuitTag)

        entry = {}

        for i in range(0,101):
            # Find first available Vyos bundle name
            lacp_name = f'bond{i}'
            if not Interface.objects.filter(device=device, name=lacp_name):
                break

        lacp = Interface(
                name=lacp_name,
                description='[RESERVED][netbox.scripts.CreateBundle]',
                type=InterfaceTypeChoices.TYPE_LAG,
                device=device,
                )

        lacp.save()

        lacp.tags.add(pni_tag)
        entry_tags = [ pni_tag.name ]

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

        int_names = {}
        for interface in interfaces:
            if interface.count_ipaddresses > 0:
                raise CancelScript(f'Interface {interface.name} already has IPs assigned to it')

            if interface.lag:
                raise CancelScript(f'Interface {interface.name} already assigned to a LAG')

            if not ( len(interface.link_peers) == 1 and 
                       isinstance(interface.link_peers[0], CircuitTermination) and
                           interface.link_peers[0].circuit.type == circuit_type ):
                raise CancelScript(f'Interface {interface.name} not wired to a valid circuit')

            cid = interface.link_peers[0].circuit.cid
            provider_name = interface.link_peers[0].circuit.provider.name
            description = f'{lacp.name}: [{provider_name}][CID: {cid}]'

            interface.lag = lacp
            interface.description = description
            interface.save()

            self.log_debug(f'interface {interface.name} assigned to {lacp_name}')
            int_names[interface.name] = { 'description' : interface.description }

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


class ConfigurePNI(Script):

    class Meta:
        name = "Configure PNI"
        description = "Configure Layer 3 for new PNI circuit" 
        scheduling_enabled = False
        commit_default = False

        fieldsets = (
            ('Interface Information', ('device', 'interface')),
            ('VLAN configuration (optional)', ('vlan_id', 'virtual_circuit_id')),
            ('Peer Information', ('peer_asn',)),
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
                'device_id': '$device',
                'tag' : slugify(PNICircuitTag),
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
            )

    virtual_circuit_id = StringVar(
            label = 'Virtual Circuit ID',
            description='Virtual Circuit ID for VLANs',
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
                raise CancelScript(f"Interface {data['device']}:{data['interface']} not found or not unique")

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

        peer_asn = str(data['peer_asn'])
        virtual_circuit_id = ( data['virtual_circuit_id'] or "" )

        ipam_role = Role.objects.get(slug='pni-autogeneration-role')
        circuit_type = CircuitType.objects.get(slug=PNICircuitTypeSlug)

        # Load / create tags that will be used later in the script
        pni_tag = lazy_load_tag(PNIConfiguredTag)
        l2ptp_tag = lazy_load_tag(L2ptpTag)
        peer_asn_tag = make_semantic_tag(PeerASNTagSuffix, peer_asn)

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

            description = f'[T=pni][peer={peer_asn}][VCID: {virtual_circuit_id}]'
            if virtual_circuit_id:
               vcid_tag = make_semantic_tag(VlanVcidTagSuffix, virtual_circuit_id)
               interface.tags.add(vcid_tag)

        else:
            if interface.count_ipaddresses > 0:
                raise CancelScript(f'Interface {interface.name} already has IPs assigned to it')

            if interface.type == InterfaceTypeChoices.TYPE_LAG: 
                description = f'[T=pni][peer={peer_asn}]'
            else:
                if not ( len(interface.link_peers) == 1 and 
                           isinstance(interface.link_peers[0], CircuitTermination) and
                               interface.link_peers[0].circuit.type == circuit_type ):
                    raise CancelScript(f'Non-VLAN/LAG Interface {interface.name} not wired to a valid circuit')

                cid = interface.link_peers[0].circuit.cid
                provider_name = interface.link_peers[0].circuit.provider.name
                description = f'[T=pni][peer={peer_asn}][{provider_name}][CID: {cid}]'

        interface.description = description
        interface.save()

        entry['description'] = interface.description

        # We assume that PNIs are Layer2 PtP
        interface.tags.add(l2ptp_tag)
        interface.tags.add(pni_tag)
        interface.tags.add(peer_asn_tag)
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
                raise CancelScript(f'Autogen v4 or v6 prefix not found')

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
                raise CancelScript(f'Invalid family for one or both IP assignments')

        else:
            raise CancelScript('Either autogen must be selected or IP fields must be completed')

        entry_addr = []
        for addr in [my_ipv4, my_ipv6]:
            for a in IPAddress.objects.filter(address=addr):
                if a.assigned_object:
                    raise CancelScript(f'{addr} is already assigned')

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
