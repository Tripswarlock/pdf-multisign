"""Material 3 inspired styling for the PDF multi-signature app."""
from __future__ import annotations

PRIMARY = "#1D4ED8"
ON_PRIMARY = "#FFFFFF"
PRIMARY_CONTAINER = "#DBEAFE"
ON_PRIMARY_CONTAINER = "#172554"
SECONDARY_CONTAINER = "#DBEAFE"
ON_SECONDARY_CONTAINER = "#1E3A8A"
SURFACE = "#F8FAFF"
SURFACE_CONTAINER = "#EFF4FC"
SURFACE_CONTAINER_HIGH = "#E4ECF9"
SURFACE_VARIANT = "#DCE6F5"
ON_SURFACE = "#0F172A"
ON_SURFACE_VARIANT = "#475569"
OUTLINE = "#64748B"
OUTLINE_VARIANT = "#C3D0E4"
ERROR = "#B3261E"
SUCCESS = "#146C2E"


def stylesheet() -> str:
    return f"""
    QWidget {{
        background: {SURFACE};
        color: {ON_SURFACE};
        font-family: "Segoe UI", "Inter", sans-serif;
        font-size: 14px;
    }}
    QMainWindow, QDialog {{ background: {SURFACE}; }}

    QLabel#AppTitle {{ font-size: 22px; font-weight: 600; color: {ON_SURFACE}; }}
    QLabel#AppSubtitle {{ color: {ON_SURFACE_VARIANT}; font-size: 13px; }}
    QLabel#SectionTitle {{ font-size: 16px; font-weight: 600; color: {ON_SURFACE}; }}
    QLabel#Caption {{ color: {ON_SURFACE_VARIANT}; font-size: 12px; }}
    QLabel#Muted {{ color: {ON_SURFACE_VARIANT}; }}
    QLabel#Error {{ color: {ERROR}; }}
    QLabel#Success {{ color: {SUCCESS}; font-weight: 600; }}

    QFrame#Card {{
        background: {SURFACE};
        border: 1px solid {OUTLINE_VARIANT};
        border-radius: 16px;
    }}
    QFrame#DropZone {{
        background: {SURFACE_CONTAINER};
        border: 2px dashed {OUTLINE};
        border-radius: 14px;
    }}
    QFrame#DropZone[dragActive="true"] {{
        background: {PRIMARY_CONTAINER};
        border: 2px dashed {PRIMARY};
    }}
    QFrame#PreviewFrame {{
        background: {SURFACE_CONTAINER_HIGH};
        border: 1px solid {OUTLINE_VARIANT};
        border-radius: 12px;
    }}
    QFrame#Divider {{ background: {OUTLINE_VARIANT}; max-height: 1px; border: none; }}

    QPushButton {{
        background: transparent;
        color: {PRIMARY};
        border: 1px solid {OUTLINE};
        border-radius: 10px;
        padding: 9px 20px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {SURFACE_CONTAINER_HIGH}; }}
    QPushButton:pressed {{ background: {SURFACE_VARIANT}; }}
    QPushButton:disabled {{ color: {OUTLINE}; border-color: {OUTLINE_VARIANT}; }}

    QPushButton#Filled {{
        background: {PRIMARY};
        color: {ON_PRIMARY};
        border: none;
        border-radius: 10px;
        padding: 12px 26px;
        font-weight: 600;
        font-size: 15px;
    }}
    QPushButton#Filled:hover {{ background: #2563EB; }}
    QPushButton#Filled:pressed {{ background: #1E40AF; }}
    QPushButton#Filled:disabled {{ background: {SURFACE_VARIANT}; color: {OUTLINE}; }}

    QPushButton#Tonal {{
        background: {SECONDARY_CONTAINER};
        color: {ON_SECONDARY_CONTAINER};
        border: none;
        border-radius: 10px;
        padding: 9px 18px;
    }}
    QPushButton#Tonal:hover {{ background: #C7DBF8; }}

    QPushButton#Text {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 6px 12px;
        color: {PRIMARY};
    }}
    QPushButton#Text:hover {{ background: {SURFACE_CONTAINER_HIGH}; }}
    QPushButton#Text:pressed {{ background: {SURFACE_VARIANT}; }}

    QComboBox {{
        background: {SURFACE_CONTAINER_HIGH};
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 8px 12px;
        min-height: 20px;
    }}
    QComboBox:hover {{ background: {SURFACE_VARIANT}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        border: 1px solid {OUTLINE_VARIANT};
        border-radius: 10px;
        selection-background-color: {PRIMARY_CONTAINER};
        selection-color: {ON_PRIMARY_CONTAINER};
        padding: 4px;
    }}

    QLineEdit {{
        background: {SURFACE_CONTAINER_HIGH};
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 8px 12px;
        selection-background-color: {PRIMARY};
        selection-color: {ON_PRIMARY};
    }}
    QLineEdit:focus {{ border: 1px solid {PRIMARY}; }}
    QLineEdit:disabled {{ color: {OUTLINE}; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {OUTLINE_VARIANT};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {OUTLINE}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{
        width: 20px; height: 20px;
        border: 2px solid {ON_SURFACE_VARIANT};
        border-radius: 4px;
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        background: {PRIMARY};
        border-color: {PRIMARY};
        image: url(none);
    }}
    QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}

    QProgressBar {{
        background: {SURFACE_VARIANT};
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}

    QToolTip {{
        background: {SURFACE_CONTAINER_HIGH};
        color: {ON_SURFACE};
        border: 1px solid {OUTLINE_VARIANT};
        padding: 6px;
    }}
    """


def certificate_row_style(selected: bool) -> str:
    background = PRIMARY_CONTAINER if selected else "transparent"
    return f"""
    QPushButton {{
        background: {background};
        border: none;
        border-radius: 12px;
        text-align: left;
        padding: 10px 12px;
    }}
    QPushButton:hover {{ background: {SURFACE_CONTAINER_HIGH}; }}
    """
