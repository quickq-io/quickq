# Architecture

**quickq** follows a two-tier architecture that separates data collection from data analysis.

## System Overview

The following diagram illustrates the flow of data from authoring to analysis.

```mermaid
graph TD
    subgraph Authoring
        YAML[YAML/Python SDK] -->|Load| SQLite[OLTP SQLite]
        FHIR_In[FHIR JSON] -->|Import| SQLite
    end

    subgraph Collection
        SQLite -->|Export| FHIR_Out[FHIR Questionnaire]
        Delivery[Survey Delivery Tool] -->|Responses| SQLite
    end

    subgraph ETL
        SQLite -->|quickq refresh| DuckDB[OLAP DuckDB]
    end

    subgraph Analytics
        DuckDB -->|Query| CLI[Analytics CLI]
        DuckDB -->|View| Dashboard[BI/Reporting]
        DuckDB -->|Export| OMOP[OMOP CDM]
    end

    style SQLite fill:#f9f,stroke:#333,stroke-width:2px
    style DuckDB fill:#bbf,stroke:#333,stroke-width:2px
```

## The Two Layers

### 1. The Transactional Layer (OLTP)

Managed by **SQLite**, this layer is the source of truth for the study structure and raw response data. It is highly normalized to prevent data anomalies during the collection phase. It implements the "Instrument Plane," "Concept Plane," and "Response Plane."

### 2. The Analytical Layer (OLAP)

Managed by **DuckDB**, this layer is populated on-demand via the `quickq refresh` command. DuckDB is uniquely suited for this as it can directly attach and read from the SQLite file. This layer transforms the normalized data into a standard star schema, pre-calculates scores, and generates aggregate tables.

## Portability

Because both layers reside in local files, a researcher can commit the entire state of a study (including the analytics engine) to a version control system or share it as a single archive.
