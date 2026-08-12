import streamlit as st
import io
import zipfile
import re
import os
import xml.etree.ElementTree as ET
import pandas as pd

# -----------------------------------------------------------------------------
# PAGE CONFIGURATION
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="KMZ Intelligent Classifier & Data Tagging",
    page_icon="🗺️",
    layout="wide"
)

# -----------------------------------------------------------------------------
# LOAD EXTERNAL PROMPT / RULES
# -----------------------------------------------------------------------------
def load_external_rules(rule_filepath: str = "Command_prompt.txt") -> str:
    if os.path.exists(rule_filepath):
        try:
            with open(rule_filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""

EXTERNAL_RULES = load_external_rules()

# -----------------------------------------------------------------------------
# MULTI-CLASSIFICATION & TEXT NORMALIZATION ENGINE
# -----------------------------------------------------------------------------
class KMZIntelligentClassifier:
    """
    Engine Klasifikasi Cerdas Berdasarkan Spesifikasi Command_prompt.txt:
    - Multi-classification (Satu Placemark bisa masuk beberapa worksheet)
    - Normalisasi teks & Regex toleran variasi (NP7, NP 7, NP-7, NP_7)
    - Konteks hirarki folder (Document -> Backbone -> Pole -> Existing)
    """

    @staticmethod
    def normalize_text(text: str) -> str:
        if not text:
            return ""
        return re.sub(r'\s+', ' ', text.strip().lower())

    def classify_placemark(self, name: str, folder_path: str, desc: str) -> dict:
        norm_name = self.normalize_text(name)
        norm_folder = self.normalize_text(folder_path)
        norm_desc = self.normalize_text(desc)
        combined_context = f"{norm_folder} {norm_name} {norm_desc}"

        is_existing = any(kw in combined_context for kw in ["existing", "eks", "old", "ex", "exist"])

        categories = {
            "Tiang": [],
            "Acc Tiang": [],
            "Slag Kabel": [],
            "Closure": [],
            "OTB, ODP": []
        }

        # 1. KLASIFIKASI TIANG
        is_pole = any(kw in combined_context for kw in ["np", "te", "pole", "tiang"])
        if is_pole:
            # Tentukan REMARK jenis tiang
            pole_match = re.search(r'\b(np|pole)[\s_-]*(\d+)\b', combined_context)
            if pole_match:
                height = pole_match.group(2)
                remark_tiang = f"New Pole {height}M" if not is_existing else f"Pole Existing {height}M"
            elif "7" in combined_context or "p7m" in combined_context:
                remark_tiang = "New Pole 7M" if not is_existing else "Pole Existing"
            elif "9" in combined_context or "p9m" in combined_context:
                remark_tiang = "New Pole 9M" if not is_existing else "Pole Existing"
            else:
                remark_tiang = "Pole Existing" if is_existing else "New Pole"

            marking_tiang = name if name else "Pole"
            categories["Tiang"].append({"MARKING": marking_tiang, "REMARK": remark_tiang})

            # Multi-classification: Tiang otomatis masuk ke Acc Tiang
            categories["Acc Tiang"].append({"MARKING": marking_tiang, "REMARK": f"Acc {remark_tiang}"})

        # 2. KLASIFIKASI SLAG KABEL / SLACK
        is_slack = any(kw in combined_context for kw in ["slag", "slack", "slak", "slk", "hanger", "hgr"])
        if is_slack:
            remark_slack = "Eks Slack Support" if is_existing else "New Slack Support"
            categories["Slag Kabel"].append({"MARKING": "Slack Support", "REMARK": remark_slack})

        # 3. KLASIFIKASI CLOSURE
        is_closure = any(kw in combined_context for kw in ["closure", "clo", "cls", "clsr"])
        if is_closure:
            remark_closure = "Eks Closure" if is_existing else "New Closure"
            # Deteksi kapasitas closure jika ada
            if "48" in combined_context:
                marking_closure = "CL48"
            elif "24" in combined_context:
                marking_closure = "CL24"
            else:
                marking_closure = "CL24"

            categories["Closure"].append({"MARKING": marking_closure, "REMARK": remark_closure})

        # 4. KLASIFIKASI OTB / ODP
        is_otb_odp = any(kw in combined_context for kw in ["otb", "odp", "acpbd", "dp"])
        if is_otb_odp:
            marking_otb = desc if desc else name
            categories["OTB, ODP"].append({"MARKING": marking_otb, "REMARK": name})

        return categories

# -----------------------------------------------------------------------------
# PARSER KML/KMZ DENGAN SIMPAN HIRARKI FOLDER
# -----------------------------------------------------------------------------
def parse_kml_hierarchy(kml_content: bytes, filename: str) -> dict:
    classifier = KMZIntelligentClassifier()
    tree = ET.fromstring(kml_content)
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    
    worksheets_data = {
        "Tiang": [],
        "Acc Tiang": [],
        "Slag Kabel": [],
        "Closure": [],
        "OTB, ODP": []
    }

    def walk_element(element, folder_path="Document"):
        # Ambil nama folder/document jika ada
        name_node = element.find('kml:name', ns)
        elem_name = name_node.text.strip() if name_node is not None and name_node.text else ""

        current_path = f"{folder_path} -> {elem_name}" if elem_name else folder_path

        # Jika elemen adalah Placemark
        if element.tag.endswith('Placemark'):
            desc_node = element.find('kml:description', ns)
            desc_text = desc_node.text.strip() if desc_node is not None and desc_node.text else ""

            # Ekstraksi Koordinat (Longitude, Latitude)
            coord_node = element.find('.//kml:coordinates', ns)
            lat, lon = "", ""
            if coord_node is not None and coord_node.text:
                raw_coords = coord_node.text.strip().split()[0].split(',')
                if len(raw_coords) >= 2:
                    lon = raw_coords[0].strip()
                    lat = raw_coords[1].strip()

            # Jalankan Multi-Classification
            classified_results = classifier.classify_placemark(elem_name, current_path, desc_text)

            for sheet_name, entries in classified_results.items():
                for entry in entries:
                    worksheets_data[sheet_name].append({
                        "MARKING": entry["MARKING"],
                        "LAT": lat,
                        "LONG": lon,
                        "REMARK": entry["REMARK"]
                    })

        # Recursive walk untuk Folder & Document
        for child in element:
            if child.tag.endswith(('Folder', 'Document', 'Placemark')):
                walk_element(child, current_path)

    walk_element(tree)
    return worksheets_data

def process_kmz_file(uploaded_file):
    in_bytes = io.BytesIO(uploaded_file.getvalue())
    all_worksheets = {
        "Tiang": [],
        "Acc Tiang": [],
        "Slag Kabel": [],
        "Closure": [],
        "OTB, ODP": []
    }

    with zipfile.ZipFile(in_bytes, 'r') as zip_ref:
        for file_info in zip_ref.infolist():
            if file_info.filename.endswith('.kml'):
                kml_data = zip_ref.read(file_info.filename)
                parsed_sheets = parse_kml_hierarchy(kml_data, uploaded_file.name)
                for sheet, rows in parsed_sheets.items():
                    all_worksheets[sheet].extend(rows)

    return all_worksheets

# -----------------------------------------------------------------------------
# EXCEL GENERATOR (SANGAT RAPIH DENGAN OPENPYXL / PANDAS)
# -----------------------------------------------------------------------------
def generate_excel_bytes(worksheets_data: dict, kmz_filename: str) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, rows in worksheets_data.items():
            df = pd.DataFrame(rows)
            
            if df.empty:
                df = pd.DataFrame(columns=["NO", "MARKING", "LAT", "LONG", "REMARK"])
            else:
                df.insert(0, "NO", range(1, len(df) + 1))
                df = df[["NO", "MARKING", "LAT", "LONG", "REMARK"]]

            # Tulis data mulai baris ke-4 agar ada tempat untuk Header Utama
            df.to_excel(writer, sheet_name=sheet_name, startrow=3, index=False)

            # Styling Worksheet
            ws = writer.sheets[sheet_name]
            ws.cell(row=1, column=1, value="DATA KOORDINAT TIANG 7m,9m & TIANG EXISTING")
            ws.cell(row=2, column=1, value=f"File Sumber: {kmz_filename}")

    output.seek(0)
    return output.getvalue()

# -----------------------------------------------------------------------------
# USER INTERFACE STREAMLIT
# -----------------------------------------------------------------------------
st.title("🗺️ KMZ/KML Intelligent Classifier & Excel Tagging Tool")
st.caption("Aplikasi pemroses geospasial otomatis berdasarkan Aturan Baku Command_prompt.txt.")

if EXTERNAL_RULES:
    with st.expander("📄 Rules Executed from Command_prompt.txt"):
        st.code(EXTERNAL_RULES[:1200] + ("..." if len(EXTERNAL_RULES) > 1200 else ""), language="markdown")

uploaded_file = st.file_uploader("Pilih File KMZ/KML Surveyor", type=["kmz", "kml"])

if uploaded_file is not None:
    st.info(f"File **{uploaded_file.name}** siap diproses.")

    if st.button("🚀 PROSES & KONVERSI DENGAN INTELLIGENT CLASSIFIER", type="primary"):
        with st.spinner("Membaca struktur folder, placemark, & membuat multi-classification..."):
            worksheets_result = process_kmz_file(uploaded_file)
            excel_bytes = generate_excel_bytes(worksheets_result, uploaded_file.name)

        st.success("✓ Konversi Berhasil! Data telah diklasifikasikan ke seluruh worksheet Excel.")

        # Tampilkan Ringkasan Statistik
        st.subheader("📊 Statistik Hasil Klasifikasi Multi-Category")
        cols = st.columns(5)
        for i, (sheet, rows) in enumerate(worksheets_result.items()):
            cols[i].metric(sheet, f"{len(rows)} titik")

        # Preview Dataframe Tabbed
        st.subheader("Preview Excel Worksheets")
        tabs = st.tabs(list(worksheets_result.keys()))
        for tab, (sheet_name, rows) in zip(tabs, worksheets_result.items()):
            with tab:
                df_preview = pd.DataFrame(rows)
                if not df_preview.empty:
                    df_preview.insert(0, "NO", range(1, len(df_preview) + 1))
                st.dataframe(df_preview, use_container_width=True)

        # Download Button Excel
        output_excel_name = f"Converted_{uploaded_file.name.replace('.kmz', '').replace('.kml', '')}.xlsx"
        st.download_button(
            label="📊 DOWNLOAD FILE EXCEL CLASSIFIED (.XLSX)",
            data=excel_bytes,
            file_name=output_excel_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
