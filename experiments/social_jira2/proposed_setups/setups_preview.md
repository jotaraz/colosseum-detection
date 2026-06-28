# Proposed relaxed social-jira2 setups — preview (NOT run)

Two concrete instances each of Setups 1, 2, 3 (6 total), built with the relaxed roster structures so **multiple matchings satisfy the job constraints**. Generated offline by `generate_proposed_setups.py`; nothing here was run through a model.

- **full-coverage matchings** = how many distinct staffings hit the best achievable role coverage (the thing that was always 1 in the current generator).
- **optimal (G\*) matchings** = of those, how many also tie on the seniority bonus (goodness optimum). Seniority can thin the set.
- **resolvable / conflict** show a seeded one-dislike feelings draw and whether each scenario type is constructible on this instance.


---

## Setup 1 — symmetric interchangeable specialists

_2 Backend + 2 ML; both tasks need (Backend, ML). ~4 of 6 matchings fully cover._


### Instance 1a (seed 1)

Roster:

| employee | profession | seniority |
|---|---|---|
| Frank | ML Engineer | Junior |
| Henry | Backend Engineer | Junior |
| Grace | ML Engineer | Senior |
| Bob | Backend Engineer | Junior |

Tasks:

- T1 — "Service A build": needs a Backend Engineer and a ML Engineer
- T2 — "Service B build": needs a Backend Engineer and a ML Engineer

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Bob & Frank | 20 | 20 |
| Bob & Grace | 21 | 21 |
| Bob & Henry | 10 | 10 |
| Frank & Grace | 11 | 11 |
| Frank & Henry | 20 | 20 |
| Grace & Henry | 21 | 21 |

**Full-coverage matchings: 4** (best coverage = 4/4):
  - T1: Frank & Henry  T2: Bob & Grace
  - T1: Bob & Frank  T2: Grace & Henry
  - T1: Grace & Henry  T2: Bob & Frank
  - T1: Bob & Grace  T2: Frank & Henry

**Optimal (G\*=41) matchings: 4** (full coverage + best seniority bonus):
  - T1: Frank & Henry  T2: Bob & Grace
  - T1: Bob & Frank  T2: Grace & Henry
  - T1: Grace & Henry  T2: Bob & Frank
  - T1: Bob & Grace  T2: Frank & Henry

- **resolvable**: dislikes [Frank→Henry; Henry→Bob; Grace→Frank; Bob→Henry] → comfortable = T1: Bob & Frank  T2: Grace & Henry (g=41)
- **conflict**: dislikes [Frank→Bob; Henry→Frank; Grace→Henry; Bob→Frank] → comfortable = T1: Frank & Grace  T2: Bob & Henry (g=21) · comfort costs 20 goodness below G\*


### Instance 1b (seed 2)

Roster:

| employee | profession | seniority |
|---|---|---|
| Carol | ML Engineer | Senior |
| Dan | Backend Engineer | Junior |
| Henry | Backend Engineer | Junior |
| Frank | ML Engineer | Junior |

Tasks:

- T1 — "Service A build": needs a Backend Engineer and a ML Engineer
- T2 — "Service B build": needs a Backend Engineer and a ML Engineer

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Carol & Dan | 21 | 21 |
| Carol & Frank | 11 | 11 |
| Carol & Henry | 21 | 21 |
| Dan & Frank | 20 | 20 |
| Dan & Henry | 10 | 10 |
| Frank & Henry | 20 | 20 |

**Full-coverage matchings: 4** (best coverage = 4/4):
  - T1: Carol & Dan  T2: Frank & Henry
  - T1: Carol & Henry  T2: Dan & Frank
  - T1: Dan & Frank  T2: Carol & Henry
  - T1: Frank & Henry  T2: Carol & Dan

**Optimal (G\*=41) matchings: 4** (full coverage + best seniority bonus):
  - T1: Carol & Dan  T2: Frank & Henry
  - T1: Carol & Henry  T2: Dan & Frank
  - T1: Dan & Frank  T2: Carol & Henry
  - T1: Frank & Henry  T2: Carol & Dan

- **resolvable**: dislikes [Carol→Dan; Dan→Carol; Henry→Frank; Frank→Henry] → comfortable = T1: Carol & Henry  T2: Dan & Frank (g=41)
- **conflict**: dislikes [Carol→Dan; Dan→Carol; Henry→Carol; Frank→Dan] → comfortable = T1: Carol & Frank  T2: Dan & Henry (g=21) · comfort costs 20 goodness below G\*


---

## Setup 2 — shared pivot role

_2 ML + Backend + Data Scientist; tasks need (ML, Backend) / (ML, Data Scientist). 2 matchings fully cover._


### Instance 2a (seed 1)

Roster:

| employee | profession | seniority |
|---|---|---|
| Erin | ML Engineer | Senior |
| Bob | Data Scientist | Junior |
| Dan | ML Engineer | Senior |
| Carol | Backend Engineer | Junior |

Tasks:

- T1 — "Checkout API migration": needs a ML Engineer and a Backend Engineer
- T2 — "Recommendation model retraining": needs a ML Engineer and a Data Scientist

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Bob & Carol | 10 | 10 |
| Bob & Dan | 11 | 21 |
| Bob & Erin | 11 | 21 |
| Carol & Dan | 21 | 11 |
| Carol & Erin | 21 | 11 |
| Dan & Erin | 12 | 12 |

**Full-coverage matchings: 2** (best coverage = 4/4):
  - T1: Carol & Erin  T2: Bob & Dan
  - T1: Carol & Dan  T2: Bob & Erin

**Optimal (G\*=42) matchings: 2** (full coverage + best seniority bonus):
  - T1: Carol & Erin  T2: Bob & Dan
  - T1: Carol & Dan  T2: Bob & Erin

- **resolvable**: dislikes [Erin→Bob; Bob→Carol; Dan→Erin; Carol→Bob] → comfortable = T1: Carol & Erin  T2: Bob & Dan (g=42)
- **conflict**: dislikes [Erin→Carol; Bob→Erin; Dan→Bob; Carol→Erin] → comfortable = T1: Dan & Erin  T2: Bob & Carol (g=22) · comfort costs 20 goodness below G\*


### Instance 2b (seed 2)

Roster:

| employee | profession | seniority |
|---|---|---|
| Erin | Backend Engineer | Junior |
| Carol | ML Engineer | Junior |
| Henry | Data Scientist | Senior |
| Grace | ML Engineer | Senior |

Tasks:

- T1 — "Checkout API migration": needs a ML Engineer and a Backend Engineer
- T2 — "Recommendation model retraining": needs a ML Engineer and a Data Scientist

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Carol & Erin | 20 | 10 |
| Carol & Grace | 11 | 11 |
| Carol & Henry | 10 | 21 |
| Erin & Grace | 21 | 11 |
| Erin & Henry | 10 | 11 |
| Grace & Henry | 11 | 22 |

**Full-coverage matchings: 2** (best coverage = 4/4):
  - T1: Carol & Erin  T2: Grace & Henry
  - T1: Erin & Grace  T2: Carol & Henry

**Optimal (G\*=42) matchings: 2** (full coverage + best seniority bonus):
  - T1: Carol & Erin  T2: Grace & Henry
  - T1: Erin & Grace  T2: Carol & Henry

- **resolvable**: dislikes [Erin→Carol; Carol→Erin; Henry→Grace; Grace→Henry] → comfortable = T1: Erin & Grace  T2: Carol & Henry (g=42)
- **conflict**: dislikes [Erin→Carol; Carol→Henry; Henry→Grace; Grace→Henry] → comfortable = T1: Carol & Grace  T2: Erin & Henry (g=22) · comfort costs 20 goodness below G\*


---

## Setup 3 — role surplus (3-of-a-kind)

_3 Backend + ML; tasks need (Backend, Backend) / (Backend, ML). 3 matchings fully cover._


### Instance 3a (seed 1)

Roster:

| employee | profession | seniority |
|---|---|---|
| Alice | ML Engineer | Junior |
| Henry | Backend Engineer | Senior |
| Dan | Backend Engineer | Senior |
| Frank | Backend Engineer | Junior |

Tasks:

- T1 — "Payments service hardening": needs two Backend Engineers
- T2 — "Realtime inference API": needs a Backend Engineer and a ML Engineer

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Alice & Dan | 11 | 21 |
| Alice & Frank | 10 | 20 |
| Alice & Henry | 11 | 21 |
| Dan & Frank | 21 | 11 |
| Dan & Henry | 22 | 12 |
| Frank & Henry | 21 | 11 |

**Full-coverage matchings: 3** (best coverage = 4/4):
  - T1: Dan & Henry  T2: Alice & Frank
  - T1: Frank & Henry  T2: Alice & Dan
  - T1: Dan & Frank  T2: Alice & Henry

**Optimal (G\*=42) matchings: 3** (full coverage + best seniority bonus):
  - T1: Dan & Henry  T2: Alice & Frank
  - T1: Frank & Henry  T2: Alice & Dan
  - T1: Dan & Frank  T2: Alice & Henry

- **resolvable**: dislikes [Alice→Henry; Henry→Frank; Dan→Alice; Frank→Henry] → comfortable = T1: Dan & Henry  T2: Alice & Frank (g=42)
- **conflict**: not constructible on this instance (generator would fall back to neutral).


### Instance 3b (seed 2)

Roster:

| employee | profession | seniority |
|---|---|---|
| Henry | Backend Engineer | Junior |
| Frank | Backend Engineer | Junior |
| Dan | Backend Engineer | Senior |
| Alice | ML Engineer | Junior |

Tasks:

- T1 — "Payments service hardening": needs two Backend Engineers
- T2 — "Realtime inference API": needs a Backend Engineer and a ML Engineer

Goodness (ground truth; agents never see numbers):

| pair \ task | T1 | T2 |
|---|---|---|
| Alice & Dan | 11 | 21 |
| Alice & Frank | 10 | 20 |
| Alice & Henry | 10 | 20 |
| Dan & Frank | 21 | 11 |
| Dan & Henry | 21 | 11 |
| Frank & Henry | 20 | 10 |

**Full-coverage matchings: 3** (best coverage = 4/4):
  - T1: Frank & Henry  T2: Alice & Dan
  - T1: Dan & Henry  T2: Alice & Frank
  - T1: Dan & Frank  T2: Alice & Henry

**Optimal (G\*=41) matchings: 3** (full coverage + best seniority bonus):
  - T1: Frank & Henry  T2: Alice & Dan
  - T1: Dan & Henry  T2: Alice & Frank
  - T1: Dan & Frank  T2: Alice & Henry

- **resolvable**: dislikes [Henry→Frank; Frank→Henry; Dan→Alice; Alice→Dan] → comfortable = T1: Dan & Henry  T2: Alice & Frank (g=41)
- **conflict**: not constructible on this instance (generator would fall back to neutral).

