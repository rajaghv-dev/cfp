# WikiCFP Search Prompts

All search queries used by the scraper. Each entry maps to a category and a list of keywords/URLs.
The scraper reads this file and crawls each URL.

Format per entry:
```
CATEGORY: <name>
KEYWORD:  <search term>   (becomes ?conference=<search term>&page=N)
URL:      <full URL>      (used as-is, page number appended if &page= or &skip= is present)
```

Lines starting with `#` are comments. Blank lines are ignored.

---

## AI — Artificial Intelligence

CATEGORY: AI
KEYWORD: artificial intelligence
KEYWORD: AI
URL: http://www.wikicfp.com/cfp/call?conference=artificial%20intelligence&page=1

---

## ML — Machine Learning

CATEGORY: ML
KEYWORD: machine learning
URL: http://www.wikicfp.com/cfp/call?conference=machine%20learning&skip=1
KEYWORD: deep learning
KEYWORD: neural network
KEYWORD: computer vision
KEYWORD: natural language processing
KEYWORD: NLP
KEYWORD: reinforcement learning
KEYWORD: large language model
KEYWORD: LLM

---

## DevOps & SRE

CATEGORY: DevOps
KEYWORD: devops
KEYWORD: site reliability
KEYWORD: platform engineering
KEYWORD: kubernetes
KEYWORD: cloud native
KEYWORD: continuous integration
KEYWORD: infrastructure as code

---

## Linux & Open Source

CATEGORY: Linux
KEYWORD: linux
KEYWORD: open source
KEYWORD: embedded linux
KEYWORD: operating systems
KEYWORD: kernel

---

## Chip Design

CATEGORY: ChipDesign
KEYWORD: VLSI
KEYWORD: chip design
KEYWORD: EDA
KEYWORD: FPGA
KEYWORD: semiconductor
KEYWORD: SoC
KEYWORD: ASIC
KEYWORD: RTL
KEYWORD: hardware design
KEYWORD: electronic design automation

---

## Mathematics

CATEGORY: Math
KEYWORD: mathematics
KEYWORD: mathematical
KEYWORD: algebra
KEYWORD: combinatorics
KEYWORD: number theory
KEYWORD: graph theory
KEYWORD: topology
KEYWORD: numerical analysis
KEYWORD: computational mathematics

---

## Legal & Cyber Law

CATEGORY: Legal
KEYWORD: law
KEYWORD: legal
KEYWORD: jurisprudence
KEYWORD: cyber law
KEYWORD: intellectual property
KEYWORD: privacy law
KEYWORD: data protection
KEYWORD: GDPR

---

## Computer Science (General)

CATEGORY: ComputerScience
URL: http://www.wikicfp.com/cfp/call?conference=computer%20science&skip=1
KEYWORD: computer science
KEYWORD: distributed systems
KEYWORD: algorithms
KEYWORD: data structures
KEYWORD: programming languages
KEYWORD: software engineering
KEYWORD: human computer interaction
KEYWORD: HCI

---

## Security & Privacy

CATEGORY: Security
URL: http://www.wikicfp.com/cfp/call?conference=security&skip=1
KEYWORD: security
KEYWORD: cybersecurity
KEYWORD: cryptography
KEYWORD: network security
KEYWORD: information security
KEYWORD: privacy
KEYWORD: blockchain

---

## Databases & Data Engineering

CATEGORY: Data
KEYWORD: database
KEYWORD: data engineering
KEYWORD: big data
KEYWORD: data science
KEYWORD: data mining
KEYWORD: knowledge discovery
KEYWORD: information retrieval

---

## Networking & Communications

CATEGORY: Networking
KEYWORD: networking
KEYWORD: wireless
KEYWORD: 5G
KEYWORD: IoT
KEYWORD: internet of things
KEYWORD: mobile computing
KEYWORD: edge computing
KEYWORD: fog computing

---

## Robotics & Automation

CATEGORY: Robotics
KEYWORD: robotics
KEYWORD: autonomous systems
KEYWORD: automation
KEYWORD: control systems

---

## Bioinformatics & Healthcare IT

CATEGORY: Bioinformatics
KEYWORD: bioinformatics
KEYWORD: computational biology
KEYWORD: health informatics
KEYWORD: medical imaging
KEYWORD: genomics

---

# ─── CONFERENCE & WORKSHOP SERIES INDEX (A–Z) ────────────────────────────────
#
# These are the WikiCFP series index pages — each letter lists every known
# conference series starting with that letter (e.g. A → AAAI, ACL, ACM ...).
# The scraper walks each index page, collects all program links, then fetches
# each series page to harvest every individual CFP entry.
#
# Format: INDEX_SERIES + letter tells the scraper to treat it as a series index.
# Two types:
#   t=c  → Conference series  (~3000+ series across A–Z)
#   t=j  → Journals & book series (separate classification)

## Conference Series Index

INDEX_SERIES: A
URL: http://www.wikicfp.com/cfp/series?t=c&i=A

INDEX_SERIES: B
URL: http://www.wikicfp.com/cfp/series?t=c&i=B

INDEX_SERIES: C
URL: http://www.wikicfp.com/cfp/series?t=c&i=C

INDEX_SERIES: D
URL: http://www.wikicfp.com/cfp/series?t=c&i=D

INDEX_SERIES: E
URL: http://www.wikicfp.com/cfp/series?t=c&i=E

INDEX_SERIES: F
URL: http://www.wikicfp.com/cfp/series?t=c&i=F

INDEX_SERIES: G
URL: http://www.wikicfp.com/cfp/series?t=c&i=G

INDEX_SERIES: H
URL: http://www.wikicfp.com/cfp/series?t=c&i=H

INDEX_SERIES: I
URL: http://www.wikicfp.com/cfp/series?t=c&i=I

INDEX_SERIES: J
URL: http://www.wikicfp.com/cfp/series?t=c&i=J

INDEX_SERIES: K
URL: http://www.wikicfp.com/cfp/series?t=c&i=K

INDEX_SERIES: L
URL: http://www.wikicfp.com/cfp/series?t=c&i=L

INDEX_SERIES: M
URL: http://www.wikicfp.com/cfp/series?t=c&i=M

INDEX_SERIES: N
URL: http://www.wikicfp.com/cfp/series?t=c&i=N

INDEX_SERIES: O
URL: http://www.wikicfp.com/cfp/series?t=c&i=O

INDEX_SERIES: P
URL: http://www.wikicfp.com/cfp/series?t=c&i=P

INDEX_SERIES: Q
URL: http://www.wikicfp.com/cfp/series?t=c&i=Q

INDEX_SERIES: R
URL: http://www.wikicfp.com/cfp/series?t=c&i=R

INDEX_SERIES: S
URL: http://www.wikicfp.com/cfp/series?t=c&i=S

INDEX_SERIES: T
URL: http://www.wikicfp.com/cfp/series?t=c&i=T

INDEX_SERIES: U
URL: http://www.wikicfp.com/cfp/series?t=c&i=U

INDEX_SERIES: V
URL: http://www.wikicfp.com/cfp/series?t=c&i=V

INDEX_SERIES: W
URL: http://www.wikicfp.com/cfp/series?t=c&i=W

INDEX_SERIES: X
URL: http://www.wikicfp.com/cfp/series?t=c&i=X

INDEX_SERIES: Y
URL: http://www.wikicfp.com/cfp/series?t=c&i=Y

INDEX_SERIES: Z
URL: http://www.wikicfp.com/cfp/series?t=c&i=Z

## Journal & Book Series Index

INDEX_JOURNAL: A
URL: http://www.wikicfp.com/cfp/series?t=j&i=A

INDEX_JOURNAL: B
URL: http://www.wikicfp.com/cfp/series?t=j&i=B

INDEX_JOURNAL: C
URL: http://www.wikicfp.com/cfp/series?t=j&i=C

INDEX_JOURNAL: D
URL: http://www.wikicfp.com/cfp/series?t=j&i=D

INDEX_JOURNAL: E
URL: http://www.wikicfp.com/cfp/series?t=j&i=E

INDEX_JOURNAL: F
URL: http://www.wikicfp.com/cfp/series?t=j&i=F

INDEX_JOURNAL: G
URL: http://www.wikicfp.com/cfp/series?t=j&i=G

INDEX_JOURNAL: H
URL: http://www.wikicfp.com/cfp/series?t=j&i=H

INDEX_JOURNAL: I
URL: http://www.wikicfp.com/cfp/series?t=j&i=I

INDEX_JOURNAL: J
URL: http://www.wikicfp.com/cfp/series?t=j&i=J

INDEX_JOURNAL: K
URL: http://www.wikicfp.com/cfp/series?t=j&i=K

INDEX_JOURNAL: L
URL: http://www.wikicfp.com/cfp/series?t=j&i=L

INDEX_JOURNAL: M
URL: http://www.wikicfp.com/cfp/series?t=j&i=M

INDEX_JOURNAL: N
URL: http://www.wikicfp.com/cfp/series?t=j&i=N

INDEX_JOURNAL: O
URL: http://www.wikicfp.com/cfp/series?t=j&i=O

INDEX_JOURNAL: P
URL: http://www.wikicfp.com/cfp/series?t=j&i=P

INDEX_JOURNAL: Q
URL: http://www.wikicfp.com/cfp/series?t=j&i=Q

INDEX_JOURNAL: R
URL: http://www.wikicfp.com/cfp/series?t=j&i=R

INDEX_JOURNAL: S
URL: http://www.wikicfp.com/cfp/series?t=j&i=S

INDEX_JOURNAL: T
URL: http://www.wikicfp.com/cfp/series?t=j&i=T

INDEX_JOURNAL: U
URL: http://www.wikicfp.com/cfp/series?t=j&i=U

INDEX_JOURNAL: V
URL: http://www.wikicfp.com/cfp/series?t=j&i=V

INDEX_JOURNAL: W
URL: http://www.wikicfp.com/cfp/series?t=j&i=W

INDEX_JOURNAL: X
URL: http://www.wikicfp.com/cfp/series?t=j&i=X

INDEX_JOURNAL: Y
URL: http://www.wikicfp.com/cfp/series?t=j&i=Y

INDEX_JOURNAL: Z
URL: http://www.wikicfp.com/cfp/series?t=j&i=Z

---

# ─── ADD YOUR PROMPTS BELOW THIS LINE ────────────────────────────────────────

