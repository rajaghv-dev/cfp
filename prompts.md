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

# ─── ADD YOUR PROMPTS BELOW THIS LINE ────────────────────────────────────────

