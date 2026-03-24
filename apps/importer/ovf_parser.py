"""Parse OVF descriptors from OVA archives.

An OVA file is a tar archive containing:
  - <name>.ovf   — XML descriptor with VM hardware specs
  - <name>.mf    — SHA manifest (optional)
  - <name>-disk1.vmdk, <name>-disk2.vmdk, ...  — disk images

This module extracts the OVF XML and returns a structured dict of
hardware specs that can be used to pre-populate the VM configure form.
"""

import logging
import os
import tarfile
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# OVF / CIM namespaces
_NS = {
    "ovf": "http://schemas.dmtf.org/ovf/envelope/1",
    "rasd": "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData",
    "vssd": "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData",
    "vmw": "http://www.vmware.com/schema/ovf",
}

# CIM ResourceType codes
_RT_CPU = "3"
_RT_MEMORY = "4"
_RT_IDE_CONTROLLER = "5"
_RT_SCSI_CONTROLLER = "6"
_RT_ETHERNET = "10"
_RT_FLOPPY = "14"
_RT_CDROM = "15"
_RT_DISK = "17"
_RT_SATA_CONTROLLER = "20"

# VMware OS type → Proxmox OS type mapping
_VMWARE_OS_MAP = {
    # Windows
    "windows9_64Guest": "win10",
    "windows9Guest": "win10",
    "windows2019srv_64Guest": "win10",
    "windows2019srvNext_64Guest": "win11",
    "windows2022srvNext_64Guest": "win11",
    "windows12_64Guest": "win11",
    "windows8_64Guest": "win10",
    "windows8Guest": "win10",
    "windows7_64Guest": "win10",
    "windows7Guest": "win10",
    "windows2012srv_64Guest": "win10",
    "win2000ServGuest": "win2k8",
    "winNetStandardGuest": "win2k8",
    "winNetEnterprise64Guest": "win2k8",
    "winLonghornGuest": "win2k8",
    "winLonghorn64Guest": "win2k8",
    "winVista64Guest": "win2k8",
    # Linux
    "centos64Guest": "l26",
    "centos7_64Guest": "l26",
    "centos8_64Guest": "l26",
    "centos9_64Guest": "l26",
    "rhel6_64Guest": "l26",
    "rhel7_64Guest": "l26",
    "rhel8_64Guest": "l26",
    "rhel9_64Guest": "l26",
    "ubuntu64Guest": "l26",
    "debian10_64Guest": "l26",
    "debian11_64Guest": "l26",
    "debian12_64Guest": "l26",
    "sles12_64Guest": "l26",
    "sles15_64Guest": "l26",
    "other3xLinux64Guest": "l26",
    "other4xLinux64Guest": "l26",
    "other5xLinux64Guest": "l26",
    "otherLinux64Guest": "l26",
    "otherLinuxGuest": "l26",
    "oracleLinux64Guest": "l26",
    "amazonlinux2_64Guest": "l26",
    "rockylinux_64Guest": "l26",
    "almalinux_64Guest": "l26",
    # Other
    "freebsd64Guest": "other",
    "other64Guest": "other",
    "otherGuest": "other",
}

# SCSI controller subtype → Proxmox disk bus recommendation
_SCSI_SUBTYPES = {
    "lsilogic": "scsi",
    "lsilogicsas": "scsi",
    "virtualscsi": "scsi",
    "buslogic": "scsi",
    "pvscsi": "scsi",
}


def parse_ovf_from_ova(ova_path):
    """Parse the OVF descriptor inside an OVA archive.

    Returns a dict with extracted hardware specs, or None if no OVF found.

    Result structure:
    {
        "vm_name": "BackupAssure-Pro",
        "os_type": "l26",
        "os_description": "Rocky Linux (64-bit)",
        "vmware_os_type": "centos64Guest",
        "cpus": 4,
        "memory_mb": 12288,
        "firmware": "efi",          # "bios" or "efi"
        "secure_boot": False,
        "disk_controller": "scsi",  # "scsi", "ide", "sata"
        "nic_type": "vmxnet3",      # "vmxnet3", "e1000", "e1000e", etc.
        "disks": [
            {"href": "disk1.vmdk", "disk_id": "vmdisk1", "capacity_gb": 40},
            {"href": "disk2.vmdk", "disk_id": "vmdisk2", "capacity_gb": 100},
        ],
    }
    """
    try:
        with tarfile.open(ova_path, "r") as tar:
            ovf_member = None
            for member in tar.getmembers():
                if member.name.endswith(".ovf"):
                    ovf_member = member
                    break
            if not ovf_member:
                logger.warning("No .ovf file found in OVA: %s", ova_path)
                return None

            f = tar.extractfile(ovf_member)
            if f is None:
                logger.warning("Could not read OVF from OVA: %s", ova_path)
                return None

            return _parse_ovf_xml(f.read().decode("utf-8"))
    except tarfile.TarError as exc:
        logger.error("Failed to open OVA as tar: %s — %s", ova_path, exc)
        return None


def parse_ovf_string(ovf_xml):
    """Parse an OVF XML string directly (for testing)."""
    return _parse_ovf_xml(ovf_xml)


def _parse_ovf_xml(ovf_xml):
    """Parse OVF XML content and return hardware specs dict."""
    try:
        root = ET.fromstring(ovf_xml)
    except ET.ParseError as exc:
        logger.error("Failed to parse OVF XML: %s", exc)
        return None

    result = {
        "vm_name": "",
        "os_type": "other",
        "os_description": "",
        "vmware_os_type": "",
        "cpus": 0,
        "memory_mb": 0,
        "firmware": "bios",
        "secure_boot": False,
        "disk_controller": "sata",
        "nic_type": "",
        "disks": [],
        "iso_file": "",  # ISO boot image from CD-ROM item
    }

    # ── Disk references from <References> and <DiskSection> ──────────
    file_refs = {}  # ovf:id -> href (filename)
    for file_el in root.findall(".//ovf:References/ovf:File", _NS):
        file_id = file_el.get(f"{{{_NS['ovf']}}}id", "")
        href = file_el.get(f"{{{_NS['ovf']}}}href", "")
        if file_id and href:
            file_refs[file_id] = href

    disk_info = {}  # diskId -> {capacity_gb, file_href}
    for disk_el in root.findall(".//ovf:DiskSection/ovf:Disk", _NS):
        disk_id = disk_el.get(f"{{{_NS['ovf']}}}diskId", "")
        capacity = disk_el.get(f"{{{_NS['ovf']}}}capacity", "0")
        units = disk_el.get(f"{{{_NS['ovf']}}}capacityAllocationUnits", "byte")
        file_ref = disk_el.get(f"{{{_NS['ovf']}}}fileRef", "")

        capacity_gb = _parse_capacity_gb(capacity, units)
        file_href = file_refs.get(file_ref, "")

        disk_info[disk_id] = {
            "href": file_href,
            "disk_id": disk_id,
            "capacity_gb": capacity_gb,
        }

    # ── VirtualSystem ────────────────────────────────────────────────
    vs = root.find(".//ovf:VirtualSystem", _NS)
    if vs is None:
        logger.warning("No VirtualSystem found in OVF")
        return result

    # VM name
    name_el = vs.find("ovf:Name", _NS)
    if name_el is not None and name_el.text:
        result["vm_name"] = name_el.text.strip()

    # OS type
    os_section = vs.find("ovf:OperatingSystemSection", _NS)
    if os_section is not None:
        vmw_os = os_section.get(f"{{{_NS['vmw']}}}osType", "").lstrip("*")
        result["vmware_os_type"] = vmw_os
        result["os_type"] = _VMWARE_OS_MAP.get(vmw_os, "other")

        desc_el = os_section.find("ovf:Description", _NS)
        if desc_el is not None and desc_el.text:
            result["os_description"] = desc_el.text.strip()

    # ── VirtualHardwareSection ───────────────────────────────────────
    hw = vs.find("ovf:VirtualHardwareSection", _NS)
    if hw is None:
        result["disks"] = list(disk_info.values())
        return result

    # Determine default deployment configuration (if any)
    default_config = None
    deploy_section = root.find(".//ovf:DeploymentOptionSection", _NS)
    if deploy_section is not None:
        for cfg in deploy_section.findall("ovf:Configuration", _NS):
            if cfg.get(f"{{{_NS['ovf']}}}default", "").lower() == "true":
                default_config = cfg.get(f"{{{_NS['ovf']}}}id", "")
                break
        # If no default marked, use the first one
        if default_config is None:
            first = deploy_section.find("ovf:Configuration", _NS)
            if first is not None:
                default_config = first.get(f"{{{_NS['ovf']}}}id", "")

    controllers = {}  # instanceID -> controller type

    for item in hw.findall("ovf:Item", _NS):
        # Skip items for non-default deployment configurations
        item_config = item.get(f"{{{_NS['ovf']}}}configuration", "")
        if item_config and default_config and item_config != default_config:
            continue
        rt = _text(item, "rasd:ResourceType")
        instance_id = _text(item, "rasd:InstanceID")
        sub_type = _text(item, "rasd:ResourceSubType").lower()

        if rt == _RT_CPU:
            qty = _text(item, "rasd:VirtualQuantity")
            if qty:
                result["cpus"] = int(qty)

        elif rt == _RT_MEMORY:
            qty = _text(item, "rasd:VirtualQuantity")
            units = _text(item, "rasd:AllocationUnits")
            if qty:
                result["memory_mb"] = _parse_memory_mb(int(qty), units)

        elif rt == _RT_SCSI_CONTROLLER:
            controllers[instance_id] = "scsi"

        elif rt == _RT_IDE_CONTROLLER:
            controllers[instance_id] = "ide"

        elif rt == _RT_SATA_CONTROLLER:
            controllers[instance_id] = "sata"

        elif rt == _RT_DISK:
            host_res = _text(item, "rasd:HostResource")
            parent = _text(item, "rasd:Parent")
            # host_res is like "ovf:/disk/vmdisk1"
            ref_id = host_res.rsplit("/", 1)[-1] if "/" in host_res else host_res
            if ref_id in disk_info:
                disk_entry = disk_info[ref_id]
                # Attach controller type from parent
                disk_entry["controller"] = controllers.get(parent, "")

        elif rt == _RT_CDROM:
            # CD-ROM item — check if it references an ISO file
            host_res = _text(item, "rasd:HostResource")
            parent = _text(item, "rasd:Parent")
            # host_res may be "ovf:/file/fileN" or "ovf:/disk/diskN"
            ref_id = host_res.rsplit("/", 1)[-1] if "/" in host_res else host_res
            iso_href = file_refs.get(ref_id, "")
            if iso_href and iso_href.lower().endswith(".iso"):
                result["iso_file"] = iso_href
                logger.info("OVF: detected ISO boot image: %s", iso_href)

        elif rt == _RT_ETHERNET:
            if sub_type:
                result["nic_type"] = sub_type

    # Set disk controller from the first disk's parent controller
    for d in disk_info.values():
        ctrl = d.pop("controller", "")
        if ctrl:
            result["disk_controller"] = ctrl
            break

    # Build ordered disk list
    result["disks"] = list(disk_info.values())

    # ── VMware-specific config (firmware, secure boot) ───────────────
    for vmw_config in hw.findall("vmw:Config", _NS):
        key = vmw_config.get(f"{{{_NS['vmw']}}}key", "")
        value = vmw_config.get(f"{{{_NS['vmw']}}}value", "")

        if key == "firmware":
            result["firmware"] = "ovmf" if value == "efi" else "seabios"
        elif key == "bootOptions.efiSecureBootEnabled":
            result["secure_boot"] = value.lower() == "true"

    return result


def ovf_to_form_defaults(ovf_data):
    """Convert parsed OVF data to form field defaults for VMConfigForm.

    Returns a dict of form field names → values that can be merged into
    the form's initial data.
    """
    if not ovf_data:
        return {}

    defaults = {}

    if ovf_data.get("vm_name"):
        from apps.importer.forms import sanitize_vm_name
        defaults["vm_name"] = sanitize_vm_name(ovf_data["vm_name"])

    if ovf_data.get("cpus"):
        defaults["cores"] = ovf_data["cpus"]

    if ovf_data.get("memory_mb"):
        defaults["memory_mb"] = ovf_data["memory_mb"]

    if ovf_data.get("os_type"):
        defaults["os_type"] = ovf_data["os_type"]

    if ovf_data.get("firmware"):
        defaults["bios"] = ovf_data["firmware"]
        if ovf_data["firmware"] == "ovmf":
            defaults["efi_disk"] = True
            defaults["machine"] = "q35"
        else:
            defaults["machine"] = "pc"

    if ovf_data.get("secure_boot"):
        defaults["secure_boot_keys"] = True

    # NIC type mapping: VMware names → Proxmox form values
    nic_map = {
        "vmxnet3": "vmxnet3",
        "e1000": "e1000",
        "e1000e": "e1000e",
        "pcnet32": "rtl8139",
    }
    nic = ovf_data.get("nic_type", "")
    if nic in nic_map:
        defaults["net_model"] = nic_map[nic]
    else:
        defaults["net_model"] = "e1000"

    # Disk controller → bus type
    ctrl = ovf_data.get("disk_controller", "")
    if ctrl == "ide":
        defaults["disk_bus"] = "ide"
    elif ctrl == "sata":
        defaults["disk_bus"] = "sata"
    else:
        defaults["disk_bus"] = "scsi"

    defaults["vga_type"] = "std"
    defaults["disk_cache"] = "none"

    return defaults


def list_ova_disk_files(ova_path):
    """List all disk image files inside an OVA archive.

    Returns a list of filenames (from tar members) that are disk images,
    ordered as they appear in the OVF if available, or by tar order.
    """
    ovf_data = parse_ovf_from_ova(ova_path)
    if ovf_data and ovf_data["disks"]:
        return [d["href"] for d in ovf_data["disks"]]

    # Fallback: list all vmdk/raw/qcow2 files from tar
    disk_exts = {".vmdk", ".raw", ".qcow2", ".img"}
    try:
        with tarfile.open(ova_path, "r") as tar:
            return [
                m.name for m in tar.getmembers()
                if os.path.splitext(m.name.lower())[1] in disk_exts
            ]
    except tarfile.TarError:
        return []


# ── Helpers ──────────────────────────────────────────────────────────────


def _text(item, tag):
    """Get text content of a child element, or empty string."""
    el = item.find(tag, _NS)
    return (el.text or "").strip() if el is not None else ""


def _parse_capacity_gb(capacity_str, units_str):
    """Convert OVF capacity + units to gigabytes."""
    try:
        capacity = int(capacity_str)
    except (ValueError, TypeError):
        return 0

    units = units_str.lower().strip()
    if "2^30" in units or "giga" in units:
        return capacity
    elif "2^20" in units or "mega" in units:
        return capacity / 1024
    elif "2^40" in units or "tera" in units:
        return capacity * 1024
    elif "byte" in units and "2^" not in units:
        return capacity / (1024 ** 3)
    # Default: assume GB
    return capacity


def _parse_memory_mb(quantity, units_str):
    """Convert OVF memory quantity + units to megabytes."""
    units = units_str.lower().strip()
    if "2^20" in units or "mega" in units:
        return quantity
    elif "2^30" in units or "giga" in units:
        return quantity * 1024
    elif "byte" in units and "2^" not in units:
        return quantity // (1024 * 1024)
    # Default: assume MB
    return quantity
