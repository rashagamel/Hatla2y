# Data Processing Agreement (Template)

**This is a starting template, not a legally binding agreement as written.**
Have it reviewed by a lawyer familiar with Egyptian data protection law
(Law 151/2020) before either party signs anything based on it. Bracketed
sections `[like this]` need to be filled in per pilot; anything not
bracketed is a proposed default, not a fixed term.

---

## Data Processing Agreement

Between: **[Your name / entity]** ("the Processor")
And: **[Brokerage legal name]** ("the Data Controller")

Regarding: a pilot of the AI Sales Copilot system, from **[start date]**
to **[end date]**.

### 1. Purpose

The Processor will process lead and conversation data provided by the Data
Controller solely to operate the AI Sales Copilot system for the purpose of
the pilot described in this agreement: matching leads to property
inventory, scoring lead priority, generating next-action recommendations,
and evaluating whether the system meaningfully assists the Data
Controller's sales process.

### 2. Data provided

The Data Controller will provide:

- [ ] Anonymized historical leads (target: 50-100), per the schema in
      `docs/data_request_template.csv`
- [ ] No direct identifiers (names, phone numbers, emails, national IDs,
      exact addresses) — see `docs/DATA_PROCESSING_POLICY.md`

The Processor will run `scripts/validate_data.py` against any data
received and will not proceed with ingestion if it fails validation,
notifying the Data Controller of what needs correction.

### 3. Retention

Data will be retained for the duration of the pilot plus **[default: 30
days]** afterward for review, then deleted, unless extended by mutual
written agreement.

### 4. Deletion

The Data Controller may request deletion of all its data at any time. The
Processor will complete deletion within **[default: 7 days]** of request
and confirm in writing.

### 5. Access

Access to the Data Controller's data is limited to: **[names/roles of
people who will have access]**. The Processor will not share this data
with any third party without the Data Controller's written consent.

### 6. Security

**[Describe actual measures in place — e.g. "data stored in a
password-protected local database, no public network exposure during the
pilot" — do not describe measures that aren't actually implemented.]**

### 7. Breach notification

If the Processor becomes aware of unauthorized access to or loss of the
Data Controller's data, it will notify the Data Controller within
**[default: 48 hours]** of discovery.

### 8. No resale or reuse beyond the pilot

Data provided under this agreement will not be used for any purpose beyond
this pilot, including training models for use with other brokerages,
without separate written consent.

### 9. Outcome of the pilot

At the end of the pilot period, the parties will review results together.
The Data Controller may request a summary of findings (e.g. lead
prioritization accuracy, agent usage) in a form that does not expose
individual customer data beyond what the Data Controller already provided.

---

**Signatures**

Processor: _______________________ Date: _______

Data Controller: _______________________ Date: _______

---

*Reminder: this template is a starting point prepared without legal review.
Terms in brackets and the retention/deletion/notification defaults above
are proposals to discuss, not fixed obligations, until both parties and
ideally a lawyer have reviewed the final version.*
