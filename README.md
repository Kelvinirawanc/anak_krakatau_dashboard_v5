# Anak Krakatau 2026 Impact Dashboard

Interactive data dashboard for analysing the impacts of the **2026 Anak Krakatau eruption** across Indonesia and documented regional aviation effects.

**Data analysis & dashboard by Kelvin Irawan**

---

## 1. Project Overview

The Anak Krakatau 2026 Impact Dashboard is an end-to-end data analytics project that combines:

- Web scraping
- Official-source research
- Data validation
- Regular-expression based extraction
- Data transformation
- Geographic visualization
- Interactive dashboard development
- GitHub Pages deployment
- Automated data refresh using GitHub Actions

The dashboard is designed to answer:

> **What were the major impacts of the 2026 Anak Krakatau eruption, how large were the impacts, which areas were affected, and how did the event affect aviation, health, population exposure, and surrounding regions?**

The project focuses on separating different types of impacts rather than presenting all effects as one category.

---

# 2. Main Analysis Areas

The dashboard covers six major dimensions:

### Volcanic Activity

- Volcano activity status
- Activity-level changes
- Eruption timeline
- Continuous eruption period
- Eruption activity observations

### Aviation

- Total affected flights
- Total affected passengers
- Flights affected by temporary airport closures
- Flights affected by route adjustments
- Impacted airports
- Regional aviation disruption

### Population Exposure

- Estimated population in reported ash-affected areas
- Geographic area associated with reported ash distribution

### Health Response

- Hospitals prepared
- Puskesmas prepared
- Affected kabupaten/kota
- Reported cumulative ISPA cases

### Regional Effects

- Indonesia ash-affected regions
- Singapore aviation disruption
- Malaysia Airlines disruption
- Malaysia ash-arrival monitoring

### Geographic Coverage

- Approximate ash-affected areas
- Higher-level ash monitoring area
- Impacted airports
- Regional aviation locations

---

# 3. Current Key Metrics

The dashboard currently uses the following trusted reference metrics:

| Metric | Value |
|---|---:|
| Current volcanic status | Level II — Waspada |
| Population exposed | 23.36 million |
| Affected flights | 2,300 |
| Affected passengers | 270,337 |
| Flights at temporarily closed airports | 1,397 |
| Passengers at temporarily closed airports | 177,579 |
| Flights affected by route adjustments | 922 |
| Passengers affected by route adjustments | 92,758 |
| Hospitals prepared | 413 |
| Puskesmas prepared | 830 |
| Affected kabupaten/kota | 20 |
| Cumulative ISPA cases | 16,759 |

The aviation figures distinguish the rounded total reported by Kementerian Perhubungan from the detailed operational categories.

---

# 4. Event Timeline

The dashboard records the major event milestones:

### 2 July 2026

Gunung Anak Krakatau was raised to:

**Level III — Siaga**

This marked an increase in volcanic activity requiring a higher level of monitoring and response.

### 4 September 2026 — 23:07 WIB

A continuous eruption period began.

### 6 September 2026 — 00:04 WIB

The continuous eruption period ended after approximately:

**~25 hours**

### 6–7 September 2026

Volcanic ash affected surrounding areas and caused aviation disruption at multiple airports.

### 7 September 2026

Kementerian Perhubungan reported approximately:

- 2,300 affected flights
- 270,337 affected passengers

cumulatively since 5 September.

### 21 September 2026 — 18:30 WIB

Badan Geologi lowered the volcanic activity status from:

**Level III — Siaga**

to:

**Level II — Waspada**

The latest trusted activity-status reference in this project is based on this evaluation.

---

# 5. Data Sources

The project uses a combination of official Indonesian government sources and documented regional references.

## Primary official sources

### Badan Geologi / ESDM

Used for:

- volcanic activity
- activity level
- eruption status
- official geological evaluation

Reference:

https://geologi.esdm.go.id/media-center/laporan-khusus-penurunan-tingkat-aktivitas-gunungapi-anak-krakatau-provinsi-lampung-dari-level-iii-siaga-menjadi-level-ii-waspada-tanggal-21-september-2026-pukul-18-30-wib

---

### BNPB

Used for:

- disaster response
- preparedness
- eruption-related response information

Reference:

https://bnpb.go.id/berita/update-aktivitas-gunung-anak-krakatau-status-level-iii-masih-berlaku

---

### Kementerian Perhubungan RI

Used for:

- affected flights
- affected passengers
- airport closures
- route adjustments
- aviation impact

Reference:

https://www.kemenhub.go.id/post/read/sejumlah-bandara-masih-terdampak-abu-vulkanik-gunung-anak-krakatau%2C-kemenhub-siapkan-bandara-alternatif

---

### Kementerian Kesehatan RI

Used for:

- hospitals prepared
- puskesmas prepared
- population exposure
- affected kabupaten/kota
- respiratory health response

Reference:

https://www.kemkes.go.id/id/kemenkes-siagakan-413-rumah-sakit-dan-830-puskesmas-hadapi-dampak-erupsi-anak-krakatau

---

### BMKG

Used for:

- volcanic ash monitoring
- ash distribution
- SIGMET-related monitoring
- airport / atmospheric monitoring

Reference:

https://www.bmkg.go.id/berita/utama/bmkg-terus-pantau-dampak-erupsi-gunung-anak-krakatau

---

# 6. Regional Aviation References

The project explicitly separates direct Indonesian impacts from regional aviation effects.

## Singapore

Reference:

**The Straits Times — Anak Krakatau eruption: SIA adds 8 relief flights between Jakarta and Singapore**

https://www.straitstimes.com/singapore/anak-krakatau-eruption-sia-adds-8-relief-flights-between-jakarta-and-singapore

Used to document:

- Singapore Airlines disruption
- Singapore–Jakarta route effects
- 8 relief flights
- other retimed or cancelled services

---

## Malaysia

Reference:

**The Star — Malaysia Airlines cancels 12 Jakarta flights after Anak Krakatau eruption**

https://www.thestar.com.my/news/nation/2026/09/06/malaysia-airlines-cancels-12-jakarta-flights-after-anak-krakatau-eruption

Used to document:

- Malaysia Airlines disruption
- 12 cancelled Jakarta flights
- effect on operations at Soekarno-Hatta

---

## Malaysia Ash-Risk Monitoring

Reference:

**NADMA / MetMalaysia**

https://www.nadma.gov.my/bi/media-en/news/7101-metmalaysia-anak-krakatau-ash-could-reach-malaysia-but-risk-remains-low

Used to document:

- regional ash-arrival monitoring
- low current ash-arrival risk
- weather / wind monitoring

Malaysia is therefore treated separately as:

**Aviation disruption + monitoring**

and not automatically classified as confirmed ashfall impact.

---

# 7. Source Selection Methodology

The project originally experimented with keyword-based source discovery.

The scraper architecture was designed around:

```text
Fixed reference
      ↓
Keyword search
      ↓
Candidate pages
      ↓
Relevance scoring
      ↓
Validation
      ↓
Use selected reference
      OR
Fallback to fixed reference
