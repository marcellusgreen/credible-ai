"""
DebtStack.ai - Database Schema

Core tables for corporate structure and debt data.
Based on the data model specification.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# =============================================================================
# CORE TABLES
# =============================================================================


class Company(Base):
    """Root table for public companies."""

    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Classification
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    industry: Mapped[Optional[str]] = mapped_column(String(100))

    # Identifiers
    cik: Mapped[Optional[str]] = mapped_column(String(20))  # SEC Central Index Key
    lei: Mapped[Optional[str]] = mapped_column(String(20))  # Legal Entity Identifier

    # Flexible attributes (ratings, market cap, etc.)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    entities: Mapped[list["Entity"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    debt_instruments: Mapped[list["DebtInstrument"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    cache: Mapped[Optional["CompanyCache"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    metrics: Mapped[Optional["CompanyMetrics"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    financials: Mapped[list["CompanyFinancials"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    obligor_group_financials: Mapped[list["ObligorGroupFinancials"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_companies_ticker", "ticker"),
        Index("idx_companies_cik", "cik"),
        Index("idx_companies_sector", "sector"),
    )


class Entity(Base):
    """Legal entities within a corporate structure."""

    __tablename__ = "entities"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # Identity
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(255))
    legal_name: Mapped[Optional[str]] = mapped_column(String(500))

    # Classification
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # holdco, opco, subsidiary, spv, jv, finco
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(100))
    formation_type: Mapped[Optional[str]] = mapped_column(String(50))  # LLC, Corp, LP, Ltd
    formation_date: Mapped[Optional[date]] = mapped_column(Date)

    # Hierarchy
    parent_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id")
    )
    ownership_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    structure_tier: Mapped[Optional[int]] = mapped_column(
        Integer
    )  # 1=holdco, 2=intermediate, 3=opco, 4+=sub

    # Status flags
    is_root: Mapped[bool] = mapped_column(Boolean, default=False)  # True = ultimate parent company
    is_guarantor: Mapped[bool] = mapped_column(Boolean, default=False)
    is_borrower: Mapped[bool] = mapped_column(Boolean, default=False)
    is_restricted: Mapped[bool] = mapped_column(Boolean, default=True)
    is_unrestricted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_material: Mapped[bool] = mapped_column(Boolean, default=False)
    is_domestic: Mapped[bool] = mapped_column(Boolean, default=True)
    is_dormant: Mapped[bool] = mapped_column(Boolean, default=False)

    # VIE and consolidation
    is_vie: Mapped[bool] = mapped_column(Boolean, default=False)
    vie_primary_beneficiary: Mapped[bool] = mapped_column(Boolean, default=False)
    consolidation_method: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # full, equity_method, proportional, vie

    # Flexible attributes
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="entities")
    parent: Mapped[Optional["Entity"]] = relationship(
        back_populates="children", remote_side=[id]
    )
    children: Mapped[list["Entity"]] = relationship(back_populates="parent")
    issued_debt: Mapped[list["DebtInstrument"]] = relationship(
        back_populates="issuer", foreign_keys="DebtInstrument.issuer_id"
    )
    guarantees: Mapped[list["Guarantee"]] = relationship(
        back_populates="guarantor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_entities_company_slug"),
        Index("idx_entities_company", "company_id"),
        Index("idx_entities_parent", "parent_id"),
        Index("idx_entities_type", "company_id", "entity_type"),
        Index(
            "idx_entities_guarantor",
            "company_id",
            "is_guarantor",
            postgresql_where=(is_guarantor == True),
        ),
        Index(
            "idx_entities_unrestricted",
            "company_id",
            "is_unrestricted",
            postgresql_where=(is_unrestricted == True),
        ),
    )


class DebtInstrument(Base):
    """Individual debt facilities and securities."""

    __tablename__ = "debt_instruments"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    issuer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )

    # Identity
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[Optional[str]] = mapped_column(String(255))
    cusip: Mapped[Optional[str]] = mapped_column(String(9))
    isin: Mapped[Optional[str]] = mapped_column(String(12))

    # Classification
    instrument_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # term_loan_b, revolver, senior_notes, etc.
    seniority: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # senior_secured, senior_unsecured, subordinated
    security_type: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # first_lien, second_lien, unsecured

    # Principal amounts (stored as BIGINT cents to avoid float issues)
    commitment: Mapped[Optional[int]] = mapped_column(BigInteger)  # Total facility size
    principal: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )  # Original principal / face value
    outstanding: Mapped[Optional[int]] = mapped_column(BigInteger)  # Current outstanding
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # Interest terms
    rate_type: Mapped[Optional[str]] = mapped_column(String(30))  # fixed, floating, unspecified
    interest_rate: Mapped[Optional[int]] = mapped_column(
        Integer
    )  # For fixed: rate in bps (850 = 8.50%)
    spread_bps: Mapped[Optional[int]] = mapped_column(
        Integer
    )  # For floating: spread over benchmark
    benchmark: Mapped[Optional[str]] = mapped_column(String(50))  # SOFR, LIBOR, Prime, or full name
    floor_bps: Mapped[Optional[int]] = mapped_column(Integer)  # Interest rate floor

    # Key dates
    issue_date: Mapped[Optional[date]] = mapped_column(Date)
    issue_date_estimated: Mapped[bool] = mapped_column(Boolean, default=False)  # True if issue_date was estimated, not extracted
    maturity_date: Mapped[Optional[date]] = mapped_column(Date)

    # Status
    is_drawn: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Data quality - indicates confidence in guarantee data completeness
    # Values: 'verified' (from Exhibit 22), 'extracted' (from LLM), 'partial', 'unknown'
    guarantee_data_confidence: Mapped[Optional[str]] = mapped_column(String(20), default="unknown")

    # Data quality - indicates confidence in collateral data completeness
    # Values: 'verified', 'extracted', 'partial', 'unknown'
    collateral_data_confidence: Mapped[Optional[str]] = mapped_column(String(20), default="unknown")

    # Flexible attributes (covenants, call schedules, etc.)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="debt_instruments")
    issuer: Mapped["Entity"] = relationship(
        back_populates="issued_debt", foreign_keys=[issuer_id]
    )
    guarantees: Mapped[list["Guarantee"]] = relationship(
        back_populates="debt_instrument", cascade="all, delete-orphan"
    )
    collateral: Mapped[list["Collateral"]] = relationship(
        back_populates="debt_instrument", cascade="all, delete-orphan"
    )
    document_links: Mapped[list["DebtInstrumentDocument"]] = relationship(
        back_populates="debt_instrument", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_debt_company_slug"),
        Index("idx_debt_company", "company_id"),
        Index("idx_debt_issuer", "issuer_id"),
        Index("idx_debt_maturity", "maturity_date"),
        Index("idx_debt_type", "instrument_type"),
        Index("idx_debt_seniority", "company_id", "seniority"),
        Index(
            "idx_debt_active",
            "company_id",
            "is_active",
            postgresql_where=(is_active == True),
        ),
    )


class BondPricing(Base):
    """
    Daily bond pricing from FINRA TRACE.

    Stores real-time pricing data for debt instruments with CUSIPs.
    Updated daily via batch job that scrapes FINRA TRACE.
    """

    __tablename__ = "bond_pricing"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    debt_instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("debt_instruments.id", ondelete="CASCADE"), nullable=False
    )
    cusip: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)  # Optional for estimated pricing

    # Pricing (clean price as percentage of par, e.g., 92.5000 = 92.5% of face value)
    last_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    last_trade_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_trade_volume: Mapped[Optional[int]] = mapped_column(BigInteger)  # Face value in cents

    # Yields (stored in basis points for precision)
    ytm_bps: Mapped[Optional[int]] = mapped_column(Integer)  # Yield to maturity in bps (682 = 6.82%)
    spread_to_treasury_bps: Mapped[Optional[int]] = mapped_column(Integer)  # Spread over benchmark
    treasury_benchmark: Mapped[Optional[str]] = mapped_column(String(10))  # "2Y", "5Y", "10Y", "30Y"

    # Quality indicators
    price_source: Mapped[str] = mapped_column(String(20), default="TRACE")  # TRACE, estimated, manual
    staleness_days: Mapped[Optional[int]] = mapped_column(Integer)  # Days since last trade

    # Timestamps
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    calculated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relationship
    debt_instrument: Mapped["DebtInstrument"] = relationship(
        backref="pricing"
    )

    __table_args__ = (
        Index("idx_bond_pricing_debt", "debt_instrument_id"),
        Index("idx_bond_pricing_cusip", "cusip"),
        Index("idx_bond_pricing_staleness", "staleness_days"),
    )


class DocumentSection(Base):
    """
    Extracted sections from SEC filings for full-text search.

    Stores sections like debt footnotes, MD&A, credit agreements, etc.
    with PostgreSQL full-text search capabilities (TSVECTOR + GIN index).

    Section types:
    - exhibit_21: Subsidiary list from 10-K Exhibit 21
    - debt_footnote: Long-term debt details from Notes
    - mda_liquidity: Liquidity and Capital Resources from MD&A
    - credit_agreement: Credit facility terms from 8-K Exhibit 10
    - guarantor_list: Guarantor subsidiaries from Notes
    - covenants: Financial covenants from Notes/Exhibits
    """

    __tablename__ = "document_sections"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # Document metadata
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # '10-K', '10-Q', '8-K'
    filing_date: Mapped[date] = mapped_column(Date, nullable=False)
    section_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'debt_footnote', 'exhibit_21', etc.
    section_title: Mapped[Optional[str]] = mapped_column(String(255))

    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)

    # Full-text search vector (auto-computed via trigger in migration)
    search_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR)

    # Source reference
    sec_filing_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(backref="document_sections")
    debt_links: Mapped[list["DebtInstrumentDocument"]] = relationship(
        back_populates="document_section", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_document_sections_search_vector", "search_vector", postgresql_using="gin"),
        Index("idx_document_sections_company", "company_id"),
        Index("idx_document_sections_doc_type", "doc_type"),
        Index("idx_document_sections_section_type", "section_type"),
        Index("idx_document_sections_filing_date", "filing_date"),
        Index("idx_document_sections_company_doc_section", "company_id", "doc_type", "section_type"),
    )


class OwnershipLink(Base):
    """Complex ownership relationships between entities (multiple parents, JVs, etc.)."""

    __tablename__ = "ownership_links"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    parent_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    child_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )

    # Ownership details
    ownership_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    ownership_type: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # direct, indirect, economic_only, voting_only

    # For JVs and partnerships
    is_joint_venture: Mapped[bool] = mapped_column(Boolean, default=False)
    jv_partner_name: Mapped[Optional[str]] = mapped_column(String(255))
    consolidation_method: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # full, equity_method, proportional, vie

    # Effective dates
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    effective_to: Mapped[Optional[date]] = mapped_column(Date)  # NULL if current

    # Flexible attributes
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    parent_entity: Mapped["Entity"] = relationship(
        foreign_keys=[parent_entity_id],
        backref="owned_entities",
    )
    child_entity: Mapped["Entity"] = relationship(
        foreign_keys=[child_entity_id],
        backref="owner_entities",
    )

    __table_args__ = (
        UniqueConstraint(
            "parent_entity_id", "child_entity_id", "effective_from",
            name="uq_ownership_parent_child_date"
        ),
        Index("idx_ownership_parent", "parent_entity_id"),
        Index("idx_ownership_child", "child_entity_id"),
        Index(
            "idx_ownership_jv",
            "is_joint_venture",
            postgresql_where=(is_joint_venture == True),
        ),
    )


class Guarantee(Base):
    """Junction table linking debt instruments to guarantor entities."""

    __tablename__ = "guarantees"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    debt_instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("debt_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    guarantor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )

    guarantee_type: Mapped[str] = mapped_column(
        String(50), default="full"
    )  # full, limited, upstream, downstream, cross-stream
    limitation_amount: Mapped[Optional[int]] = mapped_column(
        BigInteger
    )  # For limited guarantees

    # Guarantee release/add conditions (extracted from indentures)
    # Example: {"release_triggers": ["sale_of_guarantor", "asset_sale_threshold_met"],
    #           "add_triggers": ["acquisition_of_domestic_subsidiary"]}
    conditions: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    debt_instrument: Mapped["DebtInstrument"] = relationship(back_populates="guarantees")
    guarantor: Mapped["Entity"] = relationship(back_populates="guarantees")

    __table_args__ = (
        UniqueConstraint(
            "debt_instrument_id", "guarantor_id", name="uq_guarantees_debt_guarantor"
        ),
        Index("idx_guarantees_debt", "debt_instrument_id"),
        Index("idx_guarantees_guarantor", "guarantor_id"),
    )


class DebtInstrumentDocument(Base):
    """Junction table linking debt instruments to their governing legal documents."""

    __tablename__ = "debt_instrument_documents"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    debt_instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("debt_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_section_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_sections.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship type: governs, supplements, amends, related
    relationship_type: Mapped[str] = mapped_column(
        String(30), default="governs", nullable=False
    )

    # Matching algorithm metadata
    match_confidence: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 3)
    )  # 0.000 - 1.000
    match_method: Mapped[Optional[str]] = mapped_column(
        String(30)
    )  # coupon_maturity, facility_type, full_text, manual
    match_evidence: Mapped[Optional[dict]] = mapped_column(JSONB)  # Signals that led to match

    # Verification status
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(50)
    )  # 'algorithm', 'user:email@example.com', etc.

    # Relationships
    debt_instrument: Mapped["DebtInstrument"] = relationship(
        back_populates="document_links"
    )
    document_section: Mapped["DocumentSection"] = relationship(
        back_populates="debt_links"
    )

    __table_args__ = (
        UniqueConstraint(
            "debt_instrument_id", "document_section_id", "relationship_type",
            name="uq_debt_doc_instrument_document_type"
        ),
        Index("ix_debt_instrument_documents_debt_id", "debt_instrument_id"),
        Index("ix_debt_instrument_documents_doc_id", "document_section_id"),
        Index("ix_debt_instrument_documents_confidence", "match_confidence"),
        Index(
            "ix_debt_instrument_documents_verified",
            "is_verified",
            postgresql_where=(is_verified == False),
        ),
    )


class Collateral(Base):
    """Collateral securing a debt instrument (assets, equipment, real estate, etc.)."""

    __tablename__ = "collateral"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    debt_instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("debt_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Collateral type: real_estate, equipment, receivables, inventory, securities, vehicles, ip, cash, general_lien
    collateral_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)  # Free text description
    estimated_value: Mapped[Optional[int]] = mapped_column(BigInteger)  # Value in cents if disclosed
    priority: Mapped[Optional[str]] = mapped_column(String(20))  # first_lien, second_lien, etc.

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    debt_instrument: Mapped["DebtInstrument"] = relationship(back_populates="collateral")

    __table_args__ = (
        Index("ix_collateral_debt_instrument_id", "debt_instrument_id"),
        Index("ix_collateral_collateral_type", "collateral_type"),
    )


class CrossDefaultLink(Base):
    """
    Links between debt instruments for cross-default, cross-acceleration, and pari passu relationships.

    Extracted from indentures and credit agreements.
    Examples:
    - "Default on any debt > $50M triggers cross-default on this facility"
    - "Notes rank pari passu with all other senior unsecured obligations"
    """

    __tablename__ = "cross_default_links"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    source_debt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("debt_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_debt_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("debt_instruments.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Relationship type: cross_default, cross_acceleration, pari_passu
    relationship_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Threshold details
    threshold_amount: Mapped[Optional[int]] = mapped_column(BigInteger)  # in cents
    threshold_description: Mapped[Optional[str]] = mapped_column(Text)

    # Flags
    is_bilateral: Mapped[bool] = mapped_column(Boolean, default=False)

    # Confidence and evidence
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))  # 0.000 - 1.000
    source_document_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_sections.id"), nullable=True
    )
    evidence: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    source_debt: Mapped["DebtInstrument"] = relationship(
        foreign_keys=[source_debt_id],
        backref="cross_default_links_as_source",
    )
    target_debt: Mapped[Optional["DebtInstrument"]] = relationship(
        foreign_keys=[target_debt_id],
        backref="cross_default_links_as_target",
    )
    source_document: Mapped[Optional["DocumentSection"]] = relationship(
        backref="cross_default_links",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_debt_id", "target_debt_id", "relationship_type",
            name="uq_cross_default_source_target_type"
        ),
        Index("ix_cross_default_links_source", "source_debt_id"),
        Index("ix_cross_default_links_target", "target_debt_id"),
        Index("ix_cross_default_links_type", "relationship_type"),
        Index("ix_cross_default_links_source_type", "source_debt_id", "relationship_type"),
    )


# =============================================================================
# DENORMALIZED TABLES (Read Path)
# =============================================================================


class CompanyCache(Base):
    """Pre-computed API responses. Serve directly with zero processing."""

    __tablename__ = "company_cache"

    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)

    # Pre-computed responses (ready to serve as-is)
    response_company: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_structure: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_debt: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_subordination: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_guarantors: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_waterfall: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Cache control
    etag: Mapped[Optional[str]] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_filing_date: Mapped[Optional[date]] = mapped_column(Date)

    # Quick access (avoid parsing JSON for simple lookups)
    total_debt: Mapped[Optional[int]] = mapped_column(BigInteger)
    entity_count: Mapped[Optional[int]] = mapped_column(Integer)
    sector: Mapped[Optional[str]] = mapped_column(String(100))

    # Extraction status tracking (for idempotent re-runs)
    # Format: {"step_name": {"status": "success|no_data|error", "attempted_at": "ISO timestamp", "details": "..."}}
    # Steps: core, document_sections, financials, hierarchy, guarantees, collateral
    extraction_status: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="cache")

    __table_args__ = (Index("idx_cache_ticker", "ticker"),)


class CompanyFinancials(Base):
    """Quarterly financial statement data from 10-Q/10-K filings."""

    __tablename__ = "company_financials"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # Period info
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-4
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    filing_type: Mapped[Optional[str]] = mapped_column(String(10))  # 10-K, 10-Q

    # Income Statement (all in cents)
    revenue: Mapped[Optional[int]] = mapped_column(BigInteger)
    cost_of_revenue: Mapped[Optional[int]] = mapped_column(BigInteger)
    gross_profit: Mapped[Optional[int]] = mapped_column(BigInteger)
    operating_income: Mapped[Optional[int]] = mapped_column(BigInteger)  # EBIT
    ebitda: Mapped[Optional[int]] = mapped_column(BigInteger)
    interest_expense: Mapped[Optional[int]] = mapped_column(BigInteger)
    net_income: Mapped[Optional[int]] = mapped_column(BigInteger)
    depreciation_amortization: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Balance Sheet (all in cents)
    cash_and_equivalents: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_current_assets: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_assets: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_current_liabilities: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_debt: Mapped[Optional[int]] = mapped_column(BigInteger)  # Cross-check with debt_instruments
    total_liabilities: Mapped[Optional[int]] = mapped_column(BigInteger)
    stockholders_equity: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Cash Flow Statement (all in cents)
    operating_cash_flow: Mapped[Optional[int]] = mapped_column(BigInteger)
    investing_cash_flow: Mapped[Optional[int]] = mapped_column(BigInteger)
    financing_cash_flow: Mapped[Optional[int]] = mapped_column(BigInteger)
    capex: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Metadata
    source_filing: Mapped[Optional[str]] = mapped_column(String(500))  # Filing URL
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    company: Mapped["Company"] = relationship(back_populates="financials")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "fiscal_quarter",
            name="uq_financials_period"
        ),
        Index("idx_financials_company", "company_id"),
        Index("idx_financials_period", "period_end_date"),
    )


class ObligorGroupFinancials(Base):
    """
    SEC Rule 13-01 Summarized Financial Information for Obligor Group.

    Companies with guaranteed debt must disclose financial data for the Obligor Group
    (Issuer + Guarantors) separately from consolidated financials. This reveals
    what assets/income creditors can actually claim vs. what leaks to unrestricted subs.

    Found in Notes to Financial Statements, typically labeled:
    - "Summarized Financial Information"
    - "Guarantor Financial Information"
    - "Condensed Consolidating Financial Information"
    """

    __tablename__ = "obligor_group_financials"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # Period info
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    filing_type: Mapped[Optional[str]] = mapped_column(String(10))

    # Disclosure metadata
    disclosure_note_number: Mapped[Optional[str]] = mapped_column(String(50))
    debt_description: Mapped[Optional[str]] = mapped_column(Text)
    related_debt_ids: Mapped[Optional[list]] = mapped_column(JSONB)

    # Obligor Group Balance Sheet (all in cents)
    og_total_assets: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_total_liabilities: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_stockholders_equity: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_intercompany_receivables: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Obligor Group Income Statement (all in cents)
    og_revenue: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_operating_income: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_ebitda: Mapped[Optional[int]] = mapped_column(BigInteger)
    og_net_income: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Consolidated totals (for leakage calculation)
    consolidated_total_assets: Mapped[Optional[int]] = mapped_column(BigInteger)
    consolidated_revenue: Mapped[Optional[int]] = mapped_column(BigInteger)
    consolidated_ebitda: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Non-guarantor subsidiaries (if disclosed separately)
    non_guarantor_assets: Mapped[Optional[int]] = mapped_column(BigInteger)
    non_guarantor_revenue: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Computed leakage metrics (stored for fast retrieval)
    # Leakage % = (Consolidated - Obligor Group) / Consolidated * 100
    asset_leakage_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    revenue_leakage_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    ebitda_leakage_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    # Metadata
    source_filing: Mapped[Optional[str]] = mapped_column(String(500))
    uncertainties: Mapped[Optional[list]] = mapped_column(JSONB)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    company: Mapped["Company"] = relationship(back_populates="obligor_group_financials")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "fiscal_quarter",
            name="uq_obligor_group_period"
        ),
        Index("idx_obligor_group_company", "company_id"),
        Index("idx_obligor_group_leakage", "asset_leakage_pct"),
    )


class CompanySnapshot(Base):
    """Point-in-time snapshot of company data for historical tracking."""

    __tablename__ = "company_snapshots"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)

    # Snapshot metadata
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'quarterly', 'monthly', 'manual'

    # Denormalized JSON snapshots
    entities_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    debt_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    metrics_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    financials_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Summary counts
    entity_count: Mapped[Optional[int]] = mapped_column(Integer)
    debt_instrument_count: Mapped[Optional[int]] = mapped_column(Integer)
    total_debt: Mapped[Optional[int]] = mapped_column(BigInteger)
    guarantor_count: Mapped[Optional[int]] = mapped_column(Integer)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    company: Mapped["Company"] = relationship(backref="snapshots")

    __table_args__ = (
        UniqueConstraint("company_id", "snapshot_date", name="uq_company_snapshots_company_date"),
        Index("idx_company_snapshots_company", "company_id"),
        Index("idx_company_snapshots_date", "snapshot_date"),
        Index("idx_company_snapshots_company_date", "company_id", "snapshot_date"),
        Index("idx_company_snapshots_ticker", "ticker"),
    )


class ExtractionMetadata(Base):
    """Extraction quality and provenance tracking per company."""

    __tablename__ = "extraction_metadata"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )

    # Extraction quality metrics
    qa_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))  # 0.00-1.00
    extraction_method: Mapped[Optional[str]] = mapped_column(String(50))  # 'gemini', 'claude', 'hybrid'
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=1)

    # Field-level confidence (JSONB)
    field_confidence: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Source filing info
    source_10k_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_10k_date: Mapped[Optional[date]] = mapped_column(Date)
    source_10q_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_10q_date: Mapped[Optional[date]] = mapped_column(Date)

    # Timestamps
    structure_extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    debt_extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    financials_extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pricing_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Data freshness indicators
    data_version: Mapped[int] = mapped_column(Integer, default=1)
    stale_after_days: Mapped[int] = mapped_column(Integer, default=90)

    # Uncertainties and warnings
    uncertainties: Mapped[list] = mapped_column(JSONB, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    company: Mapped["Company"] = relationship(backref="extraction_metadata")

    __table_args__ = (
        Index("idx_extraction_metadata_company", "company_id"),
        Index("idx_extraction_metadata_qa_score", "qa_score"),
    )


# =============================================================================
# AUTHENTICATION & BILLING TABLES
# =============================================================================


class User(Base):
    """User accounts for API access."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hash
    api_key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # First 8 chars for display

    # Subscription tier: free, starter, growth, scale, enterprise
    tier: Mapped[str] = mapped_column(String(20), default="free", nullable=False)

    # Stripe billing
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255))
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    credits: Mapped[Optional["UserCredits"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    usage_logs: Mapped[list["UsageLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_api_key_hash", "api_key_hash"),
        Index("ix_users_stripe_customer_id", "stripe_customer_id"),
    )


class UserCredits(Base):
    """Credit balance and billing cycle tracking."""

    __tablename__ = "user_credits"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Credit balance
    credits_remaining: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("1000"), nullable=False
    )
    credits_monthly_limit: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    overage_credits_used: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )

    # Billing cycle
    billing_cycle_start: Mapped[date] = mapped_column(Date, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    user: Mapped["User"] = relationship(back_populates="credits")


class UsageLog(Base):
    """API usage log for billing and analytics."""

    __tablename__ = "usage_log"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Request details
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # GET, POST
    credits_used: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Response
    response_status: Mapped[Optional[int]] = mapped_column(Integer)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer)

    # Client info
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv6 support
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    user: Mapped["User"] = relationship(back_populates="usage_logs")

    __table_args__ = (
        Index("ix_usage_log_user_id", "user_id"),
        Index("ix_usage_log_user_date", "user_id", "created_at"),
        Index("ix_usage_log_created_at", "created_at"),
        Index("ix_usage_log_endpoint", "endpoint"),
    )


# =============================================================================
# ANALYTICS TABLES
# =============================================================================


class CompanyMetrics(Base):
    """Flat table optimized for screening and filtering."""

    __tablename__ = "company_metrics"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    # Dimensions (for filtering)
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    market_cap_bucket: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # small, mid, large, mega

    # Debt totals (BIGINT cents)
    total_debt: Mapped[Optional[int]] = mapped_column(BigInteger)
    secured_debt: Mapped[Optional[int]] = mapped_column(BigInteger)
    unsecured_debt: Mapped[Optional[int]] = mapped_column(BigInteger)
    net_debt: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Ratios (pre-computed)
    leverage_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    net_leverage_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    interest_coverage: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    secured_leverage: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))

    # Maturity profile
    debt_due_1yr: Mapped[Optional[int]] = mapped_column(BigInteger)
    debt_due_2yr: Mapped[Optional[int]] = mapped_column(BigInteger)
    debt_due_3yr: Mapped[Optional[int]] = mapped_column(BigInteger)
    nearest_maturity: Mapped[Optional[date]] = mapped_column(Date)
    weighted_avg_maturity: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 1)
    )  # years

    # Structure metrics
    entity_count: Mapped[Optional[int]] = mapped_column(Integer)
    guarantor_count: Mapped[Optional[int]] = mapped_column(Integer)

    # Risk scores (DebtStack's value-add)
    subordination_risk: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # low, moderate, high
    subordination_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2))  # 0-10
    maturity_wall_risk: Mapped[Optional[str]] = mapped_column(String(20))

    # Boolean flags for fast filtering
    has_holdco_debt: Mapped[bool] = mapped_column(Boolean, default=False)
    has_opco_debt: Mapped[bool] = mapped_column(Boolean, default=False)
    has_structural_sub: Mapped[bool] = mapped_column(Boolean, default=False)
    has_unrestricted_subs: Mapped[bool] = mapped_column(Boolean, default=False)
    has_intercreditor: Mapped[bool] = mapped_column(Boolean, default=False)
    has_foreign_carveout: Mapped[bool] = mapped_column(Boolean, default=False)
    is_covenant_lite: Mapped[bool] = mapped_column(Boolean, default=False)
    has_floating_rate: Mapped[bool] = mapped_column(Boolean, default=False)
    has_pik: Mapped[bool] = mapped_column(Boolean, default=False)
    is_leveraged_loan: Mapped[bool] = mapped_column(Boolean, default=False)  # >4x leverage
    has_near_term_maturity: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # within 24 months

    # Ratings
    sp_rating: Mapped[Optional[str]] = mapped_column(String(10))
    moodys_rating: Mapped[Optional[str]] = mapped_column(String(10))
    rating_bucket: Mapped[Optional[str]] = mapped_column(
        String(20)
    )  # IG, HY-BB, HY-B, HY-CCC, NR

    # Non-guarantor subsidiary disclosure (SEC Rule 13-01)
    # Example: {"ebitda_pct": 15.3, "assets_pct": 12.1, "source": "Note 18 - Guarantor Information"}
    non_guarantor_disclosure: Mapped[Optional[dict]] = mapped_column(JSONB)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    company: Mapped["Company"] = relationship(back_populates="metrics")

    __table_args__ = (
        Index("idx_metrics_sector", "sector"),
        Index("idx_metrics_leverage", "leverage_ratio"),
        Index("idx_metrics_subordination", "subordination_risk"),
        Index("idx_metrics_maturity", "nearest_maturity"),
        Index("idx_metrics_rating", "rating_bucket"),
        Index("idx_metrics_sector_leverage", "sector", "leverage_ratio"),
        Index("idx_metrics_sector_subordination", "sector", "subordination_risk"),
        Index("idx_metrics_rating_leverage", "rating_bucket", "leverage_ratio"),
        Index(
            "idx_metrics_risk_flags",
            "subordination_risk",
            "has_structural_sub",
            "has_unrestricted_subs",
        ),
    )
