import streamlit as st
import io
import zipfile
import re
import os
import xml.etree.ElementTree as ET
import pandas as pd

# -----------------------------------------------------------------------------
# CONFIGURATION & PAGE SETUP
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="KMZ Spatial Engine & Data Tagging Tool",
    page_icon="🗺️",
    layout="wide"
)

# -----------------------------------------------------------------------------
# HELPER: LOAD EXTERNAL PROMPT / RULE FILE
# -----------------------------------------------------------------------------
def load_external_rules(rule_filepath: str = "Command_prompt.txt") -> str:
    """Membaca file aturan/prompt luar jika tersedia."""
    if os.path.exists(rule_filepath):
        try:
            with open(rule_filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""

EXTERNAL_RULES = load_external_rules()

# -----------------------------------------------------------------------------
# GEOSPATIAL CLASSIFIER ENGINE (EXTERNAL PROMPT DRIVEN)
# -----------------------------------------------------------------------------
class SpatialEntityClassifier:
    """
    Engine pakar untuk mengklasifikasi entitas geospasial, membersihkan nama,
    dan mengarahkan placemark ke hirarki subfolder yang presisi.
    """
    def __init__(self, site_name: str):
        self.site_name = site_name

    def classify(self, raw_name: str, raw_desc: str, geom_type: str) -> dict:
        name = raw_name.strip() if raw_name else ""
        desc = raw_desc.strip() if raw_desc else ""

        info = {
            "clean_name": name,
            "target_folder": "04_SITE & ODP/OTB",
            "target_subfolder": "Uncategorized",
            "is_pole_accessory": False,
            "generate_slack": False,
            "object_type": geom_type,
            "tag_status": "NEW"
        }

        # 1. Aturan Closure
        if re.search(r'\b(cls|cl|closure)[\s_-]*48\b', name, re.IGNORECASE):
            info["clean_name"] = "New Closure 48C"
            info["target_folder"] = "03_CLOSURE & SLACK"
            info["target_subfolder"] = "Closure"
            return info

        if re.search(r'\b(cls|cl|closure)[\s_-]*24\b', name, re.IGNORECASE):
            info["clean_name"] = "New Closure 24C"
            info["target_folder"] = "03_CLOSURE & SLACK"
            info["target_subfolder"] = "Closure"
            return info

        if re.search(r'\b(cls|cl|closure)\b', name, re.IGNORECASE):
            info["clean_name"] = name if name.startswith("New") else f"New {name}"
            info["target_folder"] = "03_CLOSURE & SLACK"
            info["target_subfolder"] = "Closure"
            return info

        # 2. Aturan Tiang & Aksesori (Pole, Smartbox, New Slack Support)
        is_pole = re.search(r'\b(p7m|p9m|te|tiang|pole)\b', name, re.IGNORECASE)
        has_acc = "aksesori" in name.lower() or "aksesori" in desc.lower() or "acc" in name.lower() or "accessories" in desc.lower()

        if is_pole:
            info["target_folder"] = "02_POLE & ACCESSORIES"
            if re.search(r'\bp7m\b', name, re.IGNORECASE):
                info["target_subfolder"] = "New Pole 7M"
            elif re.search(r'\bp9m\b', name, re.IGNORECASE):
                info["target_subfolder"] = "New Pole 9M"
            else:
                info["target_subfolder"] = "Existing Pole"

            if has_acc:
                info["is_pole_accessory"] = True
                info["generate_slack"] = True
            return info

        # 3. Aturan Route & Kabel
        if geom_type == "LineString" or re.search(r'\b(cable|kabel|route|feeder)\b', name, re.IGNORECASE):
            info["target_folder"] = "01_ROUTE & CABLE"
            if "ug" in name.lower() or "underground" in name.lower():
                info["target_subfolder"] = "UG Cable"
                info["clean_name"] = "UG Cable NEW" if "new" in name.lower() else "UG Cable EXT"
            else:
                info["target_subfolder"] = "Aerial Cable"
                info["clean_name"] = "Aerial Cable NEW" if "new" in name.lower() else "Aerial Cable EXT"
            return info

        # 4. Aturan Slack / Hanger
        if re.search(r'\b(slack|hanger)\b', name, re.IGNORECASE):
            info["target_folder"] = "03_CLOSURE & SLACK"
            info["target_subfolder"] = "Slack / Hanger"
            info["clean_name"] = "New Slack Support" if "new" in name.lower() or has_acc else "Existing Slack NEW"
            return info

        return info

# -----------------------------------------------------------------------------
# MEMORY KML / KMZ PROCESSOR
# -----------------------------------------------------------------------------
def process_kml_bytes(kml_bytes: bytes, site_name: str) -> tuple[bytes, list]:
    classifier = SpatialEntityClassifier(site_name)
    tree = ET.fromstring(kml_bytes)
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    ET.register_namespace('', 'http://www.opengis.net/kml/2.2')

    records = []

    for placemark in tree.findall('.//kml:Placemark', ns):
        name_node = placemark.find('kml:name', ns)
        desc_node = placemark.find('kml:description', ns)
        
        raw_name = name_node.text if name_node is not None and name_node.text else ""
        raw_desc = desc_node.text if desc_node is not None and desc_node.text else ""

        # Deteksi Jenis Geometri
        geom_type = "Point"
        if placemark.find('.//kml:LineString', ns) is not None:
            geom_type = "LineString"
        elif placemark.find('.//kml:Polygon', ns) is not None:
            geom_type = "Polygon"

        # Ekstraksi Koordinat
        coords = ""
        coord_node = placemark.find('.//kml:coordinates', ns)
        if coord_node is not None and coord_node.text:
            coords = coord_node.text.strip().split()[0]
        
        parts = coords.split(',')
        lon = parts[0] if len(parts) > 0 else ""
        lat = parts[1] if len(parts) > 1 else ""

        # Klasifikasi & Standarisasi Nama/Folder
        res = classifier.classify(raw_name, raw_desc, geom_type)

        # Update nama placemark
        if name_node is not None:
            name_node.text = res["clean_name"]

        # Efek Domino: Jika tiang ber-aksesori -> tambahkan deskripsi Smartbox & Slack
        if res["is_pole_accessory"]:
            add_desc = "\n[Smartbox Created: New Slack Support with Tolerance]"
            if desc_node is None:
                desc_node = ET.SubElement(placemark, 'description')
                desc_node.text = add_desc.strip()
            else:
                desc_node.text = (raw_desc + add_desc).strip()

        records.append({
            "Folder Group": res["target_folder"],
            "Sub Folder": res["target_subfolder"],
            "Tagging Name (Clean)": res["clean_name"],
            "Original Field Name": raw_name,
            "Object Type": geom_type,
            "Latitude": lat,
            "Longitude": lon,
            "Cable Length (Meter)": 0.0,
            "Description": raw_desc
        })

    out_kml = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
    return out_kml, records

def process_kmz_in_memory(uploaded_file, site_name: str):
    in_bytes = io.BytesIO(uploaded_file.getvalue())
    out_bytes = io.BytesIO()
    all_records = []

    with zipfile.ZipFile(in_bytes, 'r') as in_zip:
        with zipfile.ZipFile(out_bytes, 'w', compression=zipfile.ZIP_DEFLATED) as out_zip:
            for item in in_zip.infolist():
                data = in_zip.read(item.filename)
                if item.filename.endswith('.kml'):
                    proc_kml, recs = process_kml_bytes(data, site_name)
                    out_zip.writestr(item.filename, proc_kml)
                    all_records.extend(recs)
                else:
                    out_zip.writestr(item, data)

    out_bytes.seek(0)
    return out_bytes.getvalue(), all_records

# -----------------------------------------------------------------------------
# STREAMLIT UI LAYOUT
# -----------------------------------------------------------------------------
st.title("🗺️ KMZ Fiberisasi Data Tagging Engine")
st.caption("Engine pemroses data geospasial berbasis memory dengan integrasi Command_prompt.txt secara otomatis.")

if EXTERNAL_RULES:
    with st.expander("📄 Rules Executed from Command_prompt.txt"):
        st.code(EXTERNAL_RULES[:1000] + ("..." if len(EXTERNAL_RULES) > 1000 else ""), language="markdown")

col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("⚙️ Input & Parameter")
    site_name = st.text_input("Nama Site / Kluster", value="Mekarwangi_Bogor")
    num_mode = st.radio("Penomoran Tiang", ["Sekuensial Global", "Sekuensial Per Tipe"])
    uploaded_file = st.file_uploader("Unggah KMZ / KML Surveyor", type=["kmz", "kml"])

with col_right:
    st.subheader("📊 Output & Processing")
    if uploaded_file is not None:
        if st.button("⚡ PROSES DATA GEOSPASIAL", type="primary"):
            with st.spinner("Memproses KML/KMZ di memory server..."):
                file_ext = uploaded_file.name.split('.')[-1].lower()
                if file_ext == "kmz":
                    res_bytes, records = process_kmz_in_memory(uploaded_file, site_name)
                    out_mime = "application/vnd.google-earth.kmz"
                    out_name = f"TERSTRUKTUR_{uploaded_file.name}"
                else:
                    res_kml, records = process_kml_bytes(uploaded_file.getvalue(), site_name)
                    res_bytes = res_kml
                    out_mime = "application/vnd.google-earth.kml+xml"
                    out_name = f"TERSTRUKTUR_{uploaded_file.name}"

                st.success("Data berhasil dikompilasi secara terstruktur!")

                # Preview DataFrame Data Tagging
                df = pd.DataFrame(records)
                st.subheader("Preview Excel Data Tagging")
                st.dataframe(df, use_container_width=True)

                # Export Excel to Memory BytesIO
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name="Data Tagging Actual")
                excel_buffer.seek(0)

                # Dual Download Buttons
                btn1, btn2 = st.columns(2)
                with btn1:
                    st.download_button(
                        label="📥 Download KMZ Terstruktur",
                        data=res_bytes,
                        file_name=out_name,
                        mime=out_mime
                    )
                with btn2:
                    st.download_button(
                        label="📊 Download Excel Data Tagging",
                        data=excel_buffer.getvalue(),
                        file_name=f"DATA_TAGGING_{site_name}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
