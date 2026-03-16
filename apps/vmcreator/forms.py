from django import forms

from apps.importer.forms import (
    BIOS_CHOICES,
    CPU_TYPE_CHOICES,
    DISK_BUS_CHOICES,
    DISK_CACHE_CHOICES,
    NET_MODEL_CHOICES,
    OS_TYPE_CHOICES,
    VGA_TYPE_CHOICES,
)


class VmCreateConfigForm(forms.Form):
    """VM configuration for new VM creation (ISO install or blank)."""

    # --- General ---
    vm_name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"placeholder": "my-server"}))
    vmid = forms.IntegerField(required=False, min_value=100, max_value=999999999,
                              help_text="Leave blank to auto-assign.")
    node = forms.ChoiceField(choices=[])
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    os_type = forms.ChoiceField(choices=OS_TYPE_CHOICES, initial="l26")

    # --- Firmware ---
    bios = forms.ChoiceField(choices=BIOS_CHOICES, initial="seabios")
    efi_disk = forms.BooleanField(required=False)
    secure_boot_keys = forms.BooleanField(required=False)
    tpm = forms.BooleanField(required=False)

    # --- CPU ---
    cpu_type = forms.ChoiceField(choices=CPU_TYPE_CHOICES, initial="x86-64-v2-AES")
    sockets = forms.IntegerField(initial=1, min_value=1, max_value=8)
    cores = forms.IntegerField(initial=2, min_value=1, max_value=256)
    numa = forms.BooleanField(required=False)

    # --- Memory ---
    memory_mb = forms.IntegerField(initial=2048, min_value=64)
    ballooning = forms.BooleanField(required=False, initial=True)
    balloon_min_mb = forms.IntegerField(required=False, min_value=0)

    # --- Primary Disk ---
    storage_pool = forms.ChoiceField(choices=[])
    primary_disk_size = forms.IntegerField(initial=50, min_value=1, max_value=65536,
                                           help_text="Size of the primary disk in GB.")
    disk_bus = forms.ChoiceField(choices=DISK_BUS_CHOICES, initial="scsi")
    disk_cache = forms.ChoiceField(choices=DISK_CACHE_CHOICES, initial="none")
    disk_iothread = forms.BooleanField(required=False, initial=True)
    disk_discard = forms.BooleanField(required=False)
    disk_ssd = forms.BooleanField(required=False)

    # Extra disks (JSON array from JS)
    extra_disks = forms.CharField(required=False, widget=forms.HiddenInput())

    # --- Network ---
    net_bridge = forms.ChoiceField(choices=[])
    net_model = forms.ChoiceField(choices=NET_MODEL_CHOICES, initial="virtio")
    net_vlan = forms.IntegerField(required=False, min_value=1, max_value=4094)
    net_firewall = forms.BooleanField(required=False)
    net_mac = forms.CharField(max_length=17, required=False)

    # --- Display ---
    vga_type = forms.ChoiceField(choices=VGA_TYPE_CHOICES, initial="std")
    vga_memory = forms.IntegerField(required=False, initial=16, min_value=4, max_value=512)

    # --- Options ---
    qemu_agent = forms.BooleanField(required=False)
    tablet = forms.BooleanField(required=False)
    protection = forms.BooleanField(required=False)
    start_on_boot = forms.BooleanField(required=False)
    start_after_create = forms.BooleanField(required=False)
    attach_virtio_iso = forms.BooleanField(required=False)

    def __init__(self, *args, node_choices=None, storage_choices=None,
                 bridge_choices=None, config_defaults=None, **kwargs):
        super().__init__(*args, **kwargs)
        if node_choices:
            self.fields["node"].choices = node_choices
        if storage_choices:
            self.fields["storage_pool"].choices = storage_choices
        if bridge_choices:
            self.fields["net_bridge"].choices = bridge_choices
        if config_defaults is not None:
            self.fields["cores"].initial = config_defaults.default_cores
            self.fields["memory_mb"].initial = config_defaults.default_memory_mb
            if config_defaults.default_node:
                self.fields["node"].initial = config_defaults.default_node
            if config_defaults.default_storage:
                self.fields["storage_pool"].initial = config_defaults.default_storage
            if config_defaults.default_bridge:
                self.fields["net_bridge"].initial = config_defaults.default_bridge
