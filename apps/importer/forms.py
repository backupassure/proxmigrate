import logging
import os
import re

from django import forms
from django.core.validators import RegexValidator

logger = logging.getLogger(__name__)


def sanitize_vm_name(name):
    """Convert a filename stem into a valid Proxmox VM name (DNS hostname).

    Proxmox requires: letters, digits, and hyphens only; must not start/end
    with a hyphen; max 63 characters.
    """
    # Replace underscores, dots, spaces with hyphens
    name = re.sub(r"[._\s]+", "-", name)
    # Strip anything that isn't alphanumeric or hyphen
    name = re.sub(r"[^a-zA-Z0-9-]", "", name)
    # Collapse multiple hyphens
    name = re.sub(r"-{2,}", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")
    # Truncate to 63 chars (DNS label limit)
    name = name[:63].rstrip("-")
    return name or "imported-vm"


vm_name_validator = RegexValidator(
    regex=r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$",
    message="VM name must contain only letters, numbers, and hyphens, "
            "and must not start or end with a hyphen (max 63 characters).",
)

ALLOWED_EXTENSIONS = {".qcow2", ".vmdk", ".vhd", ".vhdx", ".raw", ".img", ".ova"}

CPU_TYPE_CHOICES = [
    ("host", "host"),
    ("max", "max"),
    ("kvm64", "kvm64"),
    ("x86-64-v2", "x86-64-v2"),
    ("x86-64-v2-AES", "x86-64-v2-AES"),
    ("x86-64-v3", "x86-64-v3"),
    ("x86-64-v4", "x86-64-v4"),
    ("qemu64", "qemu64"),
    ("Nehalem", "Nehalem"),
    ("Westmere", "Westmere"),
    ("SandyBridge", "SandyBridge"),
    ("IvyBridge", "IvyBridge"),
    ("Haswell", "Haswell"),
    ("Broadwell", "Broadwell"),
    ("Skylake-Client", "Skylake-Client"),
    ("Skylake-Server", "Skylake-Server"),
    ("Cascadelake-Server", "Cascadelake-Server"),
    ("Icelake-Server", "Icelake-Server"),
    ("EPYC", "EPYC"),
    ("EPYC-v2", "EPYC-v2"),
    ("EPYC-v3", "EPYC-v3"),
    ("EPYC-v4", "EPYC-v4"),
    ("EPYC-Rome", "EPYC-Rome"),
    ("EPYC-Milan", "EPYC-Milan"),
]

OS_TYPE_CHOICES = [
    ("l26", "Linux 6.x / 5.x"),
    ("l24", "Linux 4.x"),
    ("win11", "Windows 11"),
    ("win10", "Windows 10/2022"),
    ("win2k19", "Windows 2019"),
    ("win2k8", "Windows 2008/Vista"),
    ("other", "Other"),
]

BIOS_CHOICES = [
    ("seabios", "SeaBIOS (legacy)"),
    ("ovmf", "UEFI (OVMF)"),
]

MACHINE_TYPE_CHOICES = [
    ("pc", "i440FX (legacy)"),
    ("q35", "Q35 (modern, PCIe)"),
]

DISK_BUS_CHOICES = [
    ("scsi", "VirtIO-SCSI (recommended)"),
    ("sata", "SATA"),
    ("ide", "IDE"),
]

DISK_CACHE_CHOICES = [
    ("none", "No cache"),
    ("writeback", "Write back"),
    ("writethrough", "Write through"),
    ("directsync", "Direct sync"),
    ("unsafe", "Unsafe"),
]

NET_MODEL_CHOICES = [
    ("virtio", "VirtIO (recommended)"),
    ("e1000", "Intel E1000"),
    ("e1000e", "Intel E1000e"),
    ("vmxnet3", "VMware vmxnet3"),
    ("rtl8139", "Realtek RTL8139"),
]

VGA_TYPE_CHOICES = [
    ("std", "Standard"),
    ("virtio", "VirtIO"),
    ("vmware", "VMware"),
    ("qxl", "SPICE/QXL"),
    ("cirrus", "Cirrus"),
    ("none", "None"),
]


VMWARE_EXTENSIONS = {".ova", ".vmdk"}
HYPERV_EXTENSIONS = {".vhd", ".vhdx"}
KVM_EXTENSIONS = {".qcow2", ".raw", ".img"}


def detect_source_platform(filename):
    """Detect the source virtualisation platform from the filename extension.

    Returns 'vmware', 'hyperv', 'kvm', or None.
    """
    _, ext = os.path.splitext(filename.lower())
    if ext in VMWARE_EXTENSIONS:
        return "vmware"
    if ext in HYPERV_EXTENSIONS:
        return "hyperv"
    if ext in KVM_EXTENSIONS:
        return "kvm"
    return None


HARDWARE_PRESETS = {
    "Server OS": [
        {
            "key": "windows-server",
            "label": "Windows Server (2016/2019/2022)",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000e", "disk_bus": "sata",
                "os_type": "win10", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "windows-desktop",
            "label": "Windows 10 / 11",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000e", "disk_bus": "sata",
                "os_type": "win10", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "linux-ubuntu",
            "label": "Linux — Ubuntu / Debian",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "scsi",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": True, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "linux-rhel",
            "label": "Linux — RHEL / CentOS / Rocky",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "scsi",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": True, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "linux-other",
            "label": "Linux — Other",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "scsi",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": True, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "freebsd",
            "label": "FreeBSD",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "sata",
                "os_type": "other", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
    ],
    "Appliances": [
        {
            "key": "backupassure-pro",
            "label": "BackupAssure — Pro",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 12288, "net_model": "virtio", "disk_bus": "scsi",
                "os_type": "l26", "machine": "q35", "bios": "ovmf",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": True, "disk_discard": True, "disk_ssd": True,
                "ballooning": False, "qemu_agent": True, "efi_disk": True,
            },
        },
        {
            "key": "backupassure-standard",
            "label": "BackupAssure — Standard",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 12288, "net_model": "virtio", "disk_bus": "scsi",
                "os_type": "l26", "machine": "q35", "bios": "ovmf",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": True, "disk_discard": True, "disk_ssd": True,
                "ballooning": False, "qemu_agent": True, "efi_disk": True,
            },
        },
        {
            "key": "cisco-expressway",
            "label": "Cisco — Expressway / VCS",
            "config": {
                "cpu_type": "qemu64", "cores": 4, "sockets": 1,
                "memory_mb": 6144, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
                "serial_port": True,
            },
        },
        {
            "key": "cisco-cucm",
            "label": "Cisco — CUCM / UCM",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 8192, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
                "serial_port": True,
            },
        },
        {
            "key": "cisco-ise",
            "label": "Cisco — ISE",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 16384, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
                "serial_port": True,
            },
        },
        {
            "key": "cisco-fmc",
            "label": "Cisco — FMC / FTD",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 32768, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
                "serial_port": True,
            },
        },
        {
            "key": "cisco-other",
            "label": "Cisco — Other",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
                "serial_port": True,
            },
        },
        {
            "key": "aruba-clearpass",
            "label": "Aruba — ClearPass",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 8192, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "aruba-other",
            "label": "Aruba — Other",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "paloalto-firewall",
            "label": "Palo Alto — Firewall",
            "config": {
                "cpu_type": "host", "cores": 4, "sockets": 1,
                "memory_mb": 6656, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "fortinet-fortigate",
            "label": "Fortinet — FortiGate",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "sophos-firewall",
            "label": "Sophos — Firewall",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
        {
            "key": "appliance-other",
            "label": "Appliance — Other",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 4096, "net_model": "e1000", "disk_bus": "ide",
                "os_type": "l26", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
    ],
    "Other": [
        {
            "key": "other",
            "label": "Other / Unknown",
            "config": {
                "cpu_type": "host", "cores": 2, "sockets": 1,
                "memory_mb": 2048, "net_model": "e1000", "disk_bus": "sata",
                "os_type": "other", "machine": "pc", "bios": "seabios",
                "vga_type": "std", "disk_cache": "none",
                "disk_iothread": False, "ballooning": False, "qemu_agent": False,
            },
        },
    ],
}


class UploadForm(forms.Form):
    """Disk image upload form."""

    disk_image = forms.FileField(
        label="Disk Image",
        widget=forms.FileInput(
            attrs={
                "accept": ".qcow2,.vmdk,.vhd,.vhdx,.raw,.img,.ova",
            }
        ),
        help_text="Supported formats: qcow2, vmdk, vhd, vhdx, raw, img, ova",
    )

    def clean_disk_image(self):
        f = self.cleaned_data["disk_image"]
        _name, ext = os.path.splitext(f.name.lower())
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file extension: {ext!r}. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        return f


class VMConfigForm(forms.Form):
    """Full VM configuration form for creating a Proxmox VM from an uploaded image."""

    # --- General ---
    vm_name = forms.CharField(
        max_length=63,
        label="VM Name",
        validators=[vm_name_validator],
        widget=forms.TextInput(attrs={"placeholder": "my-server"}),
        help_text="Letters, numbers, and hyphens only.",
    )
    vmid = forms.IntegerField(
        required=False,
        label="VMID",
        min_value=100,
        max_value=999999999,
        help_text="Leave blank to auto-assign from pool.",
    )
    node = forms.ChoiceField(
        label="Proxmox Node",
        choices=[],
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="Description",
    )
    os_type = forms.ChoiceField(
        choices=OS_TYPE_CHOICES,
        label="OS Type",
        initial="l26",
    )

    # --- Firmware ---
    machine = forms.ChoiceField(
        choices=MACHINE_TYPE_CHOICES,
        label="Machine Type",
        initial="pc",
    )
    bios = forms.ChoiceField(
        choices=BIOS_CHOICES,
        label="BIOS Type",
        initial="seabios",
    )
    efi_disk = forms.BooleanField(
        required=False,
        label="Add EFI Disk",
        help_text="Required when using UEFI/OVMF.",
    )
    secure_boot_keys = forms.BooleanField(
        required=False,
        label="Enroll Secure Boot Keys",
    )
    tpm = forms.BooleanField(
        required=False,
        label="Add TPM 2.0 State Drive",
    )
    start_on_boot = forms.BooleanField(
        required=False,
        label="Start on Boot",
    )

    # --- CPU ---
    cpu_type = forms.ChoiceField(
        choices=CPU_TYPE_CHOICES,
        label="CPU Type",
        initial="host",
    )
    sockets = forms.IntegerField(
        initial=1,
        label="CPU Sockets",
        min_value=1,
        max_value=8,
    )
    cores = forms.IntegerField(
        initial=2,
        label="CPU Cores (per socket)",
        min_value=1,
        max_value=256,
    )
    numa = forms.BooleanField(
        required=False,
        label="Enable NUMA",
    )

    # --- Memory ---
    memory_mb = forms.IntegerField(
        initial=2048,
        label="Memory (MB)",
        min_value=64,
    )
    ballooning = forms.BooleanField(
        required=False,
        label="Enable Memory Ballooning",
        initial=False,
    )
    balloon_min_mb = forms.IntegerField(
        required=False,
        label="Balloon Minimum (MB)",
        min_value=0,
        help_text="Minimum memory with ballooning enabled.",
    )

    # --- Primary Disk ---
    disk_bus = forms.ChoiceField(
        choices=DISK_BUS_CHOICES,
        label="Disk Bus / Controller",
        initial="scsi",
    )
    storage_pool = forms.ChoiceField(
        label="Storage Pool",
        choices=[],
    )
    disk_cache = forms.ChoiceField(
        choices=DISK_CACHE_CHOICES,
        label="Disk Cache Mode",
        initial="none",
    )
    disk_iothread = forms.BooleanField(
        required=False,
        label="Enable I/O Thread",
        initial=True,
        help_text="Recommended for VirtIO-SCSI.",
    )
    disk_discard = forms.BooleanField(
        required=False,
        label="Enable Discard (TRIM)",
    )
    disk_ssd = forms.BooleanField(
        required=False,
        label="Emulate SSD",
    )

    # Extra disks (JSON array serialized by JS: [{storage, size_gb}, ...])
    extra_disks = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    # --- Network ---
    net_bridge = forms.ChoiceField(
        label="Network Bridge",
        choices=[],
    )
    net_model = forms.ChoiceField(
        choices=NET_MODEL_CHOICES,
        label="Network Model",
        initial="virtio",
    )
    net_vlan = forms.IntegerField(
        required=False,
        label="VLAN Tag",
        min_value=1,
        max_value=4094,
        help_text="Leave blank for untagged.",
    )
    net_firewall = forms.BooleanField(
        required=False,
        label="Enable Proxmox Firewall",
    )
    net_mac = forms.CharField(
        max_length=17,
        required=False,
        label="MAC Address",
        help_text="Leave blank to auto-generate. Format: AA:BB:CC:DD:EE:FF",
    )

    # --- Display ---
    vga_type = forms.ChoiceField(
        choices=VGA_TYPE_CHOICES,
        label="Display Type",
        initial="std",
    )
    vga_memory = forms.IntegerField(
        required=False,
        initial=16,
        label="Display Memory (MB)",
        min_value=4,
        max_value=512,
    )

    # --- Agent / Misc ---
    qemu_agent = forms.BooleanField(
        required=False,
        label="Enable QEMU Guest Agent",
    )
    tablet = forms.BooleanField(
        required=False,
        label="Enable USB Tablet (for VNC pointer sync)",
    )
    serial_port = forms.BooleanField(
        required=False,
        label="Add Serial Port (socket)",
        help_text="Required for some appliances (e.g. Cisco Expressway install wizard).",
    )
    protection = forms.BooleanField(
        required=False,
        label="Enable VM Protection (prevents accidental deletion)",
    )
    start_after_import = forms.BooleanField(required=False)
    virtio_iso_ref = forms.CharField(required=False, max_length=500)

    # --- Cloud-Init ---
    cloud_init_enabled = forms.BooleanField(required=False)
    ci_storage = forms.ChoiceField(required=False, choices=[])
    ci_user = forms.CharField(required=False, max_length=100)
    ci_password = forms.CharField(required=False, max_length=200, widget=forms.PasswordInput(render_value=True))
    ci_ssh_keys = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    ci_ip_config = forms.ChoiceField(
        required=False,
        choices=[("dhcp", "DHCP"), ("static", "Static"), ("none", "None")],
        initial="dhcp",
    )
    ci_ip_address = forms.CharField(required=False, max_length=50, help_text="e.g. 192.168.1.100/24")
    ci_gateway = forms.CharField(required=False, max_length=50)
    ci_nameserver = forms.CharField(required=False, max_length=100)
    ci_search_domain = forms.CharField(required=False, max_length=200)
    ci_user_data = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 8}))

    def __init__(
        self,
        *args,
        node_choices=None,
        storage_choices=None,
        bridge_choices=None,
        config_defaults=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if node_choices:
            self.fields["node"].choices = node_choices
        if storage_choices:
            self.fields["storage_pool"].choices = storage_choices
            self.fields["ci_storage"].choices = [("", "— same as primary —")] + list(storage_choices)
        if bridge_choices:
            self.fields["net_bridge"].choices = bridge_choices

        # Pre-fill defaults from ProxmoxConfig
        if config_defaults is not None:
            self.fields["cores"].initial = config_defaults.default_cores
            self.fields["memory_mb"].initial = config_defaults.default_memory_mb
            if config_defaults.default_node:
                self.fields["node"].initial = config_defaults.default_node
            if config_defaults.default_storage:
                self.fields["storage_pool"].initial = config_defaults.default_storage
            if config_defaults.default_bridge:
                self.fields["net_bridge"].initial = config_defaults.default_bridge
