---
date: 2026-04-27
authors:
  - Jacob Peters
description: >
  A field perspective on structural challenges in survey-based health and
  epidemiology research, and how quickq addresses them.
---

# What We Struggle With: Survey Data in Health and Epidemiology Research

*Jacob Peters · April 2026*

---

Early in my career as a data professional in health and epidemiology research, I spent a lot of time writing code I was not proud of. Long SAS macros that existed only to translate a sidecar data dictionary into something analyzable. R scripts that duplicated logic already duplicated in six other R scripts, each with slightly different handling of missing values, each producing slightly different results. SQL that worked only because I had memorized which columns corresponded to which questions in which instrument version, knowledge that existed nowhere in the database itself.

I was not unusual. This is the work, in most places.

quickq grew out of those years. Not as a criticism of the researchers and teams I worked with, who were doing careful and important science, but as an attempt to give the next generation of data professionals in this space a better starting point.

Some of what follows I experienced directly, and it is the part I feel most certain about. The rest I learned from conversations with colleagues across institutions, problems they were still navigating when we spoke. Both shaped what this tool is trying to be. The problems are structural: they arise from how our tool ecosystem has been assembled, not from individual carelessness.

---

## Before a single response is collected

A survey study's analytical quality is largely determined before data collection begins. These aren't problems I ran into directly, but they came up in enough conversations that I'm confident they aren't unusual.

**Using novel questions where validated instruments already exist.** The PHQ-9, GAD-7, PROMIS, and dozens of other instruments are validated, widely normed, and coded to standard vocabularies. For the constructs they cover, there is no scientific reason to author novel questions. Yet teams do, regularly, because the validated instrument is harder to find than it appears, because adapting it feels faster than referencing it, or because the team does not know it exists. The result is data that cannot be pooled with any other study that used the real instrument.

The standard vocabulary link is the part that compounds downstream. A question authored without a LOINC or SNOMED code cannot be joined to EHR data, cannot be harmonised with a registry study, and cannot be contributed to a meta-analysis without a manual crosswalk that must be written, maintained, and trusted. These are not hypothetical losses. They are a significant reason why multi-site pooled analyses take years to negotiate instead of weeks to execute.

**Question drift across waves and sub-studies.** Even within a single research group, the same construct appears in subtly different forms over time. "How many drinks do you have per week?" becomes "How many alcoholic beverages do you consume in a typical week?" across instruments created by different people at different times. Both questions end up in the database with different variable names and no formal link. Years later, when someone tries to pool them, the connection has to be reconstructed from memory or from documentation that may no longer exist, may be wrong, or may be ambiguous.

Neither problem is difficult to prevent at authoring time. Both become very difficult to fix after data collection is complete.

---

## The data model trap

This is the category of problem I encountered most consistently, and the one that generates the most sustained maintenance burden. It has a single root cause that manifests in many forms.

**The wide-table model.** The most common pattern for storing survey data is one row per respondent, one column per question. A 30-question instrument produces a 30-column table. This feels natural (it mirrors how you might lay out a spreadsheet) and most collection platforms produce it by default.

The problems accumulate quickly. When a question is added in wave two of a longitudinal study, a new column appears that does not exist in wave one. When a question is conditionally shown (asked only of respondents who answered "yes" to a prior screening item), NULL now means at least three different things: the question was not applicable due to skip logic, the question was skipped by the respondent despite being shown, or the value is genuinely missing. These are analytically distinct situations that the data model cannot represent.

The schema also has no good answer for complex question types. When a grid question appears (a matrix where each row is rated on each column), you either denormalize into dozens of columns (`q14_row1_col1`, `q14_row1_col2`, ...) or reach for JSON blobs. Multi-select questions require either a boolean column per option or a delimited string. Neither is queryable in any standard way.

**The custom code explosion that follows.** The deeper consequence is what happens when you try to analyze this data. Because there is no shared abstraction for what a question or a response *is* (no concept of question type, no concept of valid response range, no concept of skip conditions), every analytical operation must be hand-coded against the specific column layout of the specific dataset.

A data quality check that should express as "find all questions with responses outside their valid range" instead requires a function that hardcodes every question's valid range by column name. A frequency table that should express as "for each question, count each response value" instead requires a loop over specific columns with specific type-handling for each one. A check for skip logic violations (respondents who answered a question that should have been hidden) requires reconstructing the skip conditions from external documentation and applying them column by column.

I have written, and inherited, codebases where processing a single instrument for analysis required thousands of lines of R or SAS. Duplicated across papers, across analysts, across grant cycles. When the instrument was revised, every script broke. When a new analyst joined the project, they spent weeks understanding code that should not have needed to exist in the first place.

I ran into the same wall from a different angle when working on a public-facing research data warehouse. The wide format made it nearly impossible to use standard data engineering tools. Tools like dbt are designed for transparent, testable transformations with end-to-end lineage: you write your transformations as SQL, version-control them, and anyone can trace how a value in an output table was derived from a value in the source. None of that works when every instrument has a different schema and columns are added or removed between waves. Instead, I wrote Python that generated schemas dynamically at runtime. It produced outputs, but it had no lineage, no tests, and no way for a researcher to audit what had happened between the raw data and the table they were querying. The transparency that dbt is designed to provide was simply unavailable as long as questions lived in columns.

The root cause is always the same: the collection schema was also the analytical schema, and neither was designed with the other in mind. There was no intermediate layer where questions and responses were first-class entities: things you could query, filter, and aggregate uniformly regardless of type.

A row-per-response model with a proper question dimension solves this directly. "Find all out-of-range responses" is a single query, the same for every instrument. "Compute response frequencies for every question" is a single query. "Show me all questions that were correctly skipped for a given respondent" is a single query. The analytical pattern is the same regardless of whether the question is a slider, a grid, a multi-select, or a boolean. This is the abstraction the wide-table model cannot provide, and it is the reason quickq's analytical layer exists as a separate star schema rather than a view over the collection tables.

**Scoring logic embedded at collection time.** This is one I learned from colleagues rather than lived directly, but it is worth flagging. Many platforms compute derived scores (PHQ-9 total, GAD-7 severity category) at collection time and store the result alongside raw responses. This is convenient until the scoring algorithm needs to be revised. Scoring conventions do get revised as instruments are updated and norms are recalculated in the literature. When the score is pre-computed and stored, historical totals cannot be recalculated. You are left with a dataset where the scores reflect an algorithm that may have since been corrected, and the raw items needed to recompute them are present but the recalculation has never been done. The appropriate design is to store only raw responses and derive scores on demand from a versioned definition.

**No record of which instrument version a respondent saw.** This one I dealt with directly. Instruments change mid-study: a question is added, an option is reworded, a skip condition is adjusted. When it happened on projects I was supporting, there was no systematic record of which version of the instrument a given respondent received. When wave-one responses were pooled with wave-two responses, the analysis could not account for the fact that wave-one respondents had answered a different question. The difference was often small enough to rationalize away, which is part of what made it hard to flag and easy to ignore.

---

## The platform trap

The survey tool is not a repository. It is a delivery mechanism. This distinction matters enormously over the life of a study. The problems in this section are ones colleagues have described to me: structural risks that are easy to overlook until they become acute.

**Data that lives in the tool.** REDCap, Qualtrics, and similar platforms hold study data in schemas that are proprietary in practice even when technically exportable. When an institutional license expires, when a platform changes pricing, or when a collaboration partner uses a different system, the path to the data is disrupted. Studies with years of collection history have been rendered effectively inaccessible for exactly this reason. The portable artifact for a long-lived study should be a file in an open format that any tool can read without the original platform installed.

**Instruments that live only in the tool.** When a questionnaire is defined by clicking through a survey platform UI, the definition exists only inside that platform. It cannot be version-controlled, reviewed by a collaborator in a pull request, diffed between revisions, or reproduced from scratch. A question is changed and nobody knows when, by whom, or what it said before. Two researchers on the same team carry slightly different mental models of the current instrument because they viewed the UI at different times. Text-based authoring (a YAML file committed to a repository) gives you history, review, reproducibility, and a single legible source of truth.

**No standard analytical contract.** When the survey platform also does analysis (dashboards, frequency tables, summary reports), the analytical layer has no standard schema. Every study is different. There is no query pattern that works across studies, no shared understanding of what a "response" is, no way to write a generic data quality tool that runs against any questionnaire dataset. Shared analytical infrastructure requires a shared schema, and the platform's collection schema is not it.

---

## Where the debt becomes visible

By the time analysis begins, every compromise made earlier has compounded into a concrete burden. I know these patterns partly from my own work and partly from conversations with analysts and researchers who described them in terms that felt immediately recognizable.

**Scoring duplicated across scripts.** The PHQ-9 total gets independently computed, with subtly different handling of missing items, in multiple analysis scripts across the same project. When those scripts produce different numbers, and they do, determining which one is correct becomes a research task in itself. The authoritative score definition should live in one place, applied consistently from the same raw data, not scattered across supplementary methods sections.

**Cross-study harmonisation by spreadsheet.** Two studies used the same construct but different variable names, different option codings, and different instrument versions. Pooling them requires a crosswalk spreadsheet mapping one study's variables to the other's. The person who wrote the spreadsheet has left the lab. The spreadsheet has a version that differs from the one used in the published analysis. This scenario is not exceptional, and it comes up in nearly every conversation I have had about multi-study programs.

The structural solution is concept mapping. Two questions that both carry LOINC:44250-9 join to each other at query time, without a crosswalk. The harmonisation is in the data, not in documentation that can drift.

---

## The sharing trap

The hardest problems in survey-based epidemiology research arise when you try to share data or collaborate across institutions. These are problems I learned about through discussions with researchers navigating them, not ones I faced directly myself.

**Individual-level data transfer as the only option.** In most multi-institution studies, pooling data for analysis means physically transferring individual-level records to a coordinating center. This requires a data use agreement, IRB amendments at each site, legal review, and negotiation that can take months to years. Many scientifically valuable collaborations never happen because the overhead is prohibitive relative to the expected return.

Federated analysis (where each site runs a shared query against its local data and returns only aggregate results) sidesteps most of this overhead. But federated analysis only works if every site's data follows the same schema. If each site has a bespoke collection model, the coordinating center must write and validate a separate query for each site, which recreates the burden in a different form. A shared schema is the prerequisite, not an optional enhancement.

**Opaque study artifacts.** A study database shared with a collaborator is opaque without the original platform installed and the original configuration accessible. What do the column names mean? What version of the instrument was used? What are the valid response values for item 14? A study file should be self-describing: the data dictionary and rendered instrument should be derivable from the database itself, not from an external document that must be separately maintained and transmitted.

**Multi-site instrument drift.** In a distributed study, sites independently configure their collection systems. Minor differences accumulate: a question is added at one site but not another, an option is reworded, a skip condition is adjusted. When site data is merged for cross-site analysis, responses may not be directly comparable, and the divergence may not be detected until analysis is underway. Detecting and surfacing schema divergence at merge time, not at analysis time, is the correct behavior.

---

## A note on how this tool came to exist

The problems I know from direct experience are the ones in the data model section: the wide tables, the schema that changed between waves, the mystery NULL that could mean three different things, the skip logic that lived only in a PDF specification and had to be reconstructed by hand for every analysis, the sidecar data dictionary that drifted away from the data it described, the Python code I wrote to generate schemas dynamically because no standard tool could handle a schema that was different for every instrument. That work was real and it was unnecessary, and I spent years doing it.

The other sections I learned from conversations with colleagues, researchers and data professionals who described problems they were still living with. I found the patterns familiar enough that I am confident they are not isolated to any one institution or study design.

None of it reflects poorly on the researchers and teams I worked with, who were thoughtful people doing important science with the tools available to them. The problems are structural. The survey tool ecosystem has not historically made the right thing easy. It has made the expedient thing easy, and the expedient thing accumulates into years of maintenance burden.

quickq is an attempt to make the right thing the default path. That means one row per response instead of one column per question, so question type and skip logic are properties of the data rather than knowledge you carry in your head. It means versioned, immutable question definitions so you always know what a respondent was actually asked. It means vocabulary codes on questions and response options so cross-study analysis is a join, not a project. It means a portable SQLite file as the study artifact, not data locked inside a platform. None of these are novel ideas. They are established in clinical informatics and implemented at scale in systems like OMOP CDM. The contribution here is assembling them in a form that a two-person research team can adopt without a data architect, a database administrator, and a year-long implementation project.

If any of the patterns above are familiar, I hope this tool reduces the time you spend writing code that should not need to exist.
