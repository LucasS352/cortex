"""
DeskCortex — Domain Layer
=========================
Skills especialistas em domínios de negócio do ERP Master:
Fiscal (NF-e/NFC-e/SEFAZ) e E-commerce (Canais, Hubs e Integrações).
"""

from .fiscal_skill import FiscalSkill
from .ecommerce_skill import EcommerceSkill

__all__ = ["FiscalSkill", "EcommerceSkill"]
