# Crawl-source discovery ledger

Record of `/discover-crawl-sources` work against prod (`https://api.yabot.jobs`), 2026-09-19, waves 1-7. Status and ATS columns are generated from the live `GET /admin/crawl-sources` roster, so they show what prod actually holds, not what was intended. Per-request outcomes for waves 3-6 are in `docs/crawl-source-ledger.jsonl`.

**Prod totals at last update (post wave 7):** 1434 active, 39 pending, 22 rejected (session start: 191 / 11 / 10). The rejected count rose by 12 during wave 4 without any action from this session: those are earlier pending rows from this ledger (shared platform domains such as `jobs.smartrecruiters.com`, `jobs.jobvite.com`, `recruiting.ultipro.com`, `recruiting.paylocity.com`, several Taleo/iCIMS tenants) that look like they were triaged in the pending queue. Pending fell from 52 to 30 the same way (some resolved to active via new adapters, e.g. Ulta and REI now `icims`). Waves 5-6 ran later the same day after checking in with the user on scope — see their sections below. **Wave 6 exhausted the session's WebSearch budget (200/200)**, so all four of its forks (Middle East/Israel, Africa, Eastern Europe, Canada) came back well under target; the user redirected future waves to USA jobs afterward. **Wave 7 ran as a separate, concurrent session** (different sectors: K-12/higher-ed, restaurants/hospitality, sports/entertainment, and a different international slice) — see its section for the overlap this caused and how it resolved.

**How to read the tables:** *Board URL* is the literal URL submitted. **shape-only** means a board added through `POST /admin/crawl-sources`, which only checks URL shape. Workday's SPA can't be fetched, so those boards were never content-verified: a wrong tenant shows up as a `last_error` after the first crawl. Greenhouse/Lever/Ashby/Workable/BambooHR/JazzHR/Personio/Recruitee/Breezy boards were verified live via each platform's public API before adding.


## Wave 1


### Financial services & insurance (22/24 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| SoFi | https://job-boards.greenhouse.io/sofi | greenhouse | active | |
| Betterment | https://job-boards.greenhouse.io/betterment | greenhouse | active | |
| Fireblocks | https://job-boards.greenhouse.io/fireblocks | greenhouse | active | |
| Melio | https://job-boards.greenhouse.io/melio | greenhouse | active | |
| Kraken | https://jobs.ashbyhq.com/kraken.com | ashby | active | |
| Persona (identity) | https://jobs.ashbyhq.com/persona | ashby | active | |
| Column (bank) | https://jobs.ashbyhq.com/column | ashby | active | |
| Sardine | https://jobs.ashbyhq.com/sardine | ashby | active | |
| Wealthsimple | https://jobs.ashbyhq.com/wealthsimple | ashby | active | |
| Middesk | https://jobs.ashbyhq.com/middesk | ashby | active | |
| Binance | https://jobs.lever.co/binance | lever | active | |
| Greenlight | https://jobs.lever.co/greenlight | lever | active | |
| PayPal | https://paypal.eightfold.ai/careers | – | not in prod | Eightfold root 422s on the admin endpoint (no static URL shape); handled below via /jobs + PATCH |
| American Express | https://aexp.eightfold.ai/careers | – | not in prod | Eightfold root 422'd; Amex has no working Eightfold API. Placeholder row later deleted |
| Mastercard | https://mastercard.wd1.myworkdayjobs.com/CorporateCareers | workday | active | |
| Truist | https://truist.wd1.myworkdayjobs.com/Careers | workday | active | |
| BlackRock | https://blackrock.wd1.myworkdayjobs.com/BlackRock_Professional | workday | active | |
| Fifth Third Bank | https://fifththird.wd5.myworkdayjobs.com/53careers | workday | active | |
| Travelers | https://travelers.wd5.myworkdayjobs.com/External | workday | active | |
| Synchrony | https://synchronyfinancial.wd5.myworkdayjobs.com/careers | workday | active | |
| Nationwide | https://nationwide.wd1.myworkdayjobs.com/Nationwide_Career | workday | active | |
| Prudential Financial | https://pru.wd5.myworkdayjobs.com/Careers | workday | active | |
| Vanguard | https://vanguard.wd5.myworkdayjobs.com/vanguard_external | workday | active | |
| Morgan Stanley | https://ms.wd5.myworkdayjobs.com/External | workday | active | |

### Semiconductor, hardware & networking (20/20 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| SambaNova Systems | https://job-boards.greenhouse.io/sambanovasystems | greenhouse | active | |
| Lightmatter | https://job-boards.greenhouse.io/lightmatter | greenhouse | active | |
| Tenstorrent | https://job-boards.greenhouse.io/tenstorrent | greenhouse | active | |
| Astera Labs | https://job-boards.greenhouse.io/asteralabs | greenhouse | active | |
| Figure AI | https://job-boards.greenhouse.io/figureai | greenhouse | active | |
| Agility Robotics | https://job-boards.greenhouse.io/agilityrobotics | greenhouse | active | |
| CoreWeave | https://job-boards.greenhouse.io/coreweave | greenhouse | active | |
| IonQ | https://job-boards.greenhouse.io/ionq | greenhouse | active | |
| Formlabs | https://job-boards.greenhouse.io/formlabs | greenhouse | active | |
| Netskope | https://job-boards.greenhouse.io/netskope | greenhouse | active | |
| Rubrik | https://job-boards.greenhouse.io/rubrik | greenhouse | active | |
| Etched.ai | https://jobs.ashbyhq.com/etched | ashby | active | |
| Cerebras Systems | https://jobs.ashbyhq.com/cerebras | ashby | active | |
| Lambda (GPU Cloud) | https://jobs.ashbyhq.com/lambda | ashby | active | |
| Crusoe (AI Infrastructure) | https://jobs.ashbyhq.com/crusoe | ashby | active | |
| Marvell Technology | https://marvell.wd1.myworkdayjobs.com/MarvellCareers | workday | active | |
| Analog Devices | https://analogdevices.wd1.myworkdayjobs.com/External | workday | active | |
| Micron Technology | https://micron.wd1.myworkdayjobs.com/External | workday | active | |
| Cadence Design Systems | https://cadence.wd1.myworkdayjobs.com/External_Careers | workday | active | |
| Ciena | https://ciena.wd5.myworkdayjobs.com/Careers | workday | active | |

### Education & edtech (26/26 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Coursera | https://job-boards.greenhouse.io/coursera | greenhouse | active | |
| Khan Academy | https://job-boards.greenhouse.io/khanacademy | greenhouse | active | |
| Udemy | https://job-boards.greenhouse.io/udemy | greenhouse | active | |
| Outschool | https://job-boards.greenhouse.io/outschool | greenhouse | active | |
| Newsela | https://job-boards.greenhouse.io/newsela | greenhouse | active | |
| Guild Education | https://job-boards.greenhouse.io/guild | greenhouse | active | |
| MasterClass | https://job-boards.greenhouse.io/masterclass | greenhouse | active | |
| Udacity | https://job-boards.greenhouse.io/udacity | greenhouse | active | |
| Springboard | https://job-boards.greenhouse.io/springboard | greenhouse | active | |
| Edmentum | https://job-boards.greenhouse.io/edmentum | greenhouse | active | |
| Age of Learning (ABCmouse) | https://job-boards.greenhouse.io/ageoflearninginc | greenhouse | active | |
| CodePath | https://job-boards.greenhouse.io/codepath | greenhouse | active | |
| Renaissance Learning | https://job-boards.greenhouse.io/renaissancelearning-nam | greenhouse | active | |
| GiveCampus | https://job-boards.greenhouse.io/givecampus | greenhouse | active | |
| Degreed | https://job-boards.greenhouse.io/degreed | greenhouse | active | |
| Seesaw | https://job-boards.greenhouse.io/seesaw | greenhouse | active | |
| Clever | https://job-boards.greenhouse.io/clever | greenhouse | active | |
| Speak (language learning) | https://jobs.ashbyhq.com/speak | ashby | active | |
| Handshake (edtech) | https://jobs.ashbyhq.com/handshake | ashby | active | |
| Instructure (Canvas) | https://jobs.ashbyhq.com/instructure | ashby | active | |
| Ellevation Education | https://jobs.lever.co/ellevationeducation | lever | active | |
| Zen Educate | https://jobs.lever.co/zeneducate | lever | active | |
| Cengage Group | https://cengage.wd5.myworkdayjobs.com/CengageNorthAmericaCareers | workday | active | |
| Stride (K12) | https://strideinc.wd1.myworkdayjobs.com/SK | workday | active | |
| Wiley | https://wiley.wd1.myworkdayjobs.com/wiley_careers | workday | active | |
| Kaplan | https://ghc.wd1.myworkdayjobs.com/Kaplan_Careers | workday | active | |

### Real estate, proptech & construction (26/26 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| SmartRent | https://job-boards.greenhouse.io/smartrent | greenhouse | active | |
| Homeward | https://job-boards.greenhouse.io/homeward | greenhouse | active | |
| Realtor.com | https://job-boards.greenhouse.io/rdccareers | greenhouse | active | |
| Roofstock | https://job-boards.greenhouse.io/roofstock | greenhouse | active | |
| HomeLight | https://job-boards.greenhouse.io/homelight | greenhouse | active | |
| Splitero | https://job-boards.greenhouse.io/splitero | greenhouse | active | |
| Compass (real estate) | https://job-boards.greenhouse.io/urbancompass | greenhouse | active | |
| Lessen | https://jobs.lever.co/lessen | lever | active | |
| CIM Group | https://jobs.lever.co/cimgroup | lever | active | |
| Entrata | https://jobs.lever.co/entrata | lever | active | |
| Houzz | https://jobs.lever.co/houzz | lever | active | |
| EliseAI | https://jobs.ashbyhq.com/eliseai | ashby | active | |
| Juniper Square | https://jobs.ashbyhq.com/junipersquare | ashby | active | |
| Augrade | https://jobs.ashbyhq.com/augrade | ashby | active | |
| Real REMAX Group | https://jobs.ashbyhq.com/realremaxgroup | ashby | active | |
| Thumbtack | https://jobs.ashbyhq.com/thumbtack | ashby | active | |
| Zillow Group | https://zillow.wd5.myworkdayjobs.com/Zillow_Group_External | workday | active | |
| Redfin | https://redfin.wd1.myworkdayjobs.com/redfin_careers | workday | active | |
| CoStar Group | https://costar.wd1.myworkdayjobs.com/CoStarCareers | workday | active | |
| JLL | https://jll.wd1.myworkdayjobs.com/jllcareers | workday | active | |
| Cushman & Wakefield | https://cw.wd1.myworkdayjobs.com/External | workday | active | |
| Greystar | https://greystar.wd1.myworkdayjobs.com/External | workday | active | |
| Lennar | https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs | workday | active | |
| PulteGroup | https://pultegroup.wd1.myworkdayjobs.com/PGI | workday | active | |
| Trimble | https://trimble.wd1.myworkdayjobs.com/TrimbleCareers | workday | active | |
| Autodesk | https://autodesk.wd1.myworkdayjobs.com/Ext | workday | active | |

## Wave 1 follow-ups


### Boards held back from wave 1 (shape-only Workday etc.) (7/7 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| PNC Financial | https://pnc.wd5.myworkdayjobs.com/External | workday | active | |
| Citi | https://citi.wd5.myworkdayjobs.com/2 | workday | active | |
| FIS | https://fis.wd5.myworkdayjobs.com/searchjobs | workday | active | |
| Seagate Technology | https://seagate.wd1.myworkdayjobs.com/EXT | workday | active | |
| Fannie Mae | https://fanniemae.wd1.myworkdayjobs.com/FannieMaeCareers | workday | active | |
| Chegg | https://osv-chegg.wd5.myworkdayjobs.com/Chegg | workday | active | |
| Atomi | https://jobs.lever.co/atomi | lever | active | |

## Wave 2


### Biotech & pharma (32/32 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Revolution Medicines | https://job-boards.greenhouse.io/revolutionmedicines | greenhouse | active | |
| Twist Bioscience | https://job-boards.greenhouse.io/twistbioscience | greenhouse | active | |
| Nurix Therapeutics | https://job-boards.greenhouse.io/nurix | greenhouse | active | |
| Freenome | https://job-boards.greenhouse.io/freenome | greenhouse | active | |
| Adaptive Biotechnologies | https://job-boards.greenhouse.io/adaptivebiotechnologies | greenhouse | active | |
| Generate Biomedicines | https://job-boards.greenhouse.io/generatebiomedicines | greenhouse | active | |
| Relay Therapeutics | https://job-boards.greenhouse.io/relaytherapeutics | greenhouse | active | |
| Prime Medicine | https://job-boards.greenhouse.io/primemedicine | greenhouse | active | |
| Seer (proteomics) | https://job-boards.greenhouse.io/seer | greenhouse | active | |
| Arvinas | https://job-boards.greenhouse.io/arvinas | greenhouse | active | |
| Chai Discovery | https://jobs.ashbyhq.com/chaidiscovery | ashby | active | |
| Deep Genomics | https://jobs.lever.co/deepgenomics | lever | active | |
| Korro Bio | https://jobs.lever.co/korrobio | lever | active | |
| Amgen | https://amgen.wd1.myworkdayjobs.com/Careers | workday | active | |
| Merck (MSD) | https://msd.wd5.myworkdayjobs.com/SearchJobs | workday | active | |
| Pfizer | https://pfizer.wd1.myworkdayjobs.com/PfizerCareers | workday | active | |
| Eli Lilly | https://lilly.wd5.myworkdayjobs.com/LLY | workday | active | |
| Bristol Myers Squibb | https://bristolmyerssquibb.wd5.myworkdayjobs.com/BMS | workday | active | |
| Gilead Sciences | https://gilead.wd1.myworkdayjobs.com/gileadcareers | workday | active | |
| Regeneron | https://regeneron.wd1.myworkdayjobs.com/Careers | workday | active | |
| Moderna | https://modernatx.wd1.myworkdayjobs.com/M_tx | workday | active | |
| Novartis | https://novartis.wd3.myworkdayjobs.com/Novartis_Careers | workday | active | |
| Sanofi | https://sanofi.wd3.myworkdayjobs.com/SanofiCareers | workday | active | |
| GSK | https://gsk.wd5.myworkdayjobs.com/GSKCareers | workday | active | |
| Takeda | https://takeda.wd3.myworkdayjobs.com/External | workday | active | |
| Zoetis | https://zoetis.wd5.myworkdayjobs.com/zoetis | workday | active | |
| IQVIA | https://iqvia.wd1.myworkdayjobs.com/IQVIA | workday | active | |
| Labcorp | https://labcorp.wd1.myworkdayjobs.com/External | workday | active | |
| Illumina | https://illumina.wd1.myworkdayjobs.com/illumina-careers | workday | active | |
| Biogen | https://biibhr.wd3.myworkdayjobs.com/external | workday | active | |
| Abbott | https://abbott.wd5.myworkdayjobs.com/abbottcareers | workday | active | |
| Vertex Pharmaceuticals | https://vrtx.wd5.myworkdayjobs.com/Vertex_Careers | workday | active | |

### Logistics, industrial, aerospace & energy (38/38 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Relativity Space | https://job-boards.greenhouse.io/relativity | greenhouse | active | |
| SpaceX | https://job-boards.greenhouse.io/spacex | greenhouse | active | |
| Rocket Lab | https://job-boards.greenhouse.io/rocketlab | greenhouse | active | |
| Astranis | https://job-boards.greenhouse.io/astranis | greenhouse | active | |
| Planet Labs | https://job-boards.greenhouse.io/planetlabs | greenhouse | active | |
| Divergent | https://job-boards.greenhouse.io/divergent | greenhouse | active | |
| Xometry | https://job-boards.greenhouse.io/xometry | greenhouse | active | |
| Fictiv | https://job-boards.greenhouse.io/fictiv | greenhouse | active | |
| Apptronik | https://job-boards.greenhouse.io/apptronik | greenhouse | active | |
| Uber Freight | https://job-boards.greenhouse.io/uberfreight | greenhouse | active | |
| ShipMonk | https://job-boards.greenhouse.io/shipmonk | greenhouse | active | |
| ChargePoint | https://job-boards.greenhouse.io/chargepoint | greenhouse | active | |
| FourKites | https://job-boards.greenhouse.io/fourkites | greenhouse | active | |
| Outrider | https://job-boards.greenhouse.io/outrider | greenhouse | active | |
| Pivot Bio | https://job-boards.greenhouse.io/pivotbio | greenhouse | active | |
| Locus Robotics | https://job-boards.greenhouse.io/locusrobotics | greenhouse | active | |
| Flexe | https://job-boards.greenhouse.io/flexe | greenhouse | active | |
| Loadsmart | https://jobs.lever.co/loadsmart | lever | active | |
| Blue Origin | https://blueorigin.wd5.myworkdayjobs.com/BlueOrigin | workday | active | |
| AEP | https://aep.wd1.myworkdayjobs.com/AEPCareerSite | workday | active | |
| AES | https://aes.wd1.myworkdayjobs.com/AES_US | workday | active | |
| SOLV Energy | https://solvenergy.wd1.myworkdayjobs.com/SOLV_External_Career | workday | active | |
| Invenergy | https://invenergyllc.wd1.myworkdayjobs.com/invenergycareers | workday | active | |
| Moss (Moss & Associates) | https://mosscm.wd1.myworkdayjobs.com/Moss_Careers | workday | active | |
| 3M | https://3m.wd1.myworkdayjobs.com/Search | workday | active | |
| Rockwell Automation | https://rockwellautomation.wd1.myworkdayjobs.com/External_Rockwell_Automation | workday | active | |
| Sunrun | https://sunrun.wd5.myworkdayjobs.com/Sunrun_Careers | workday | active | |
| Enbridge | https://enbridge.wd3.myworkdayjobs.com/enbridge_careers | workday | active | |
| J.B. Hunt | https://jbhunt.wd5.myworkdayjobs.com/Careers | workday | active | |
| Ryder | https://ryder.wd5.myworkdayjobs.com/RyderCareers | workday | active | |
| C.H. Robinson | https://chrobinson.wd5.myworkdayjobs.com/CHRobinson | workday | active | |
| First Solar | https://firstsolar.wd1.myworkdayjobs.com/FirstSolar | workday | active | |
| Canadian Solar | https://canadiansolar.wd5.myworkdayjobs.com/CanadianSolar | workday | active | |
| Ameren | https://ameren.wd1.myworkdayjobs.com/External | workday | active | |
| Crescent Energy | https://crescentenergyco.wd108.myworkdayjobs.com/crescent_energy_careers | workday | active | |
| EPRI | https://vhr-epri.wd1.myworkdayjobs.com/epricareers | workday | active | |
| Endeavor Energy Resources | https://eeronline.wd1.myworkdayjobs.com/EndeavorEnergyCareers | workday | active | |
| E.ON Next | https://eonnext.wd3.myworkdayjobs.com/EON_Next_Careers | workday | active | |

### Healthcare & medtech (45/45 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Omada Health | https://job-boards.greenhouse.io/omadahealth | greenhouse | active | |
| Flatiron Health | https://job-boards.greenhouse.io/flatironhealth | greenhouse | active | |
| Sword Health | https://job-boards.greenhouse.io/swordhealth | greenhouse | active | |
| Talkspace | https://job-boards.greenhouse.io/talkspace | greenhouse | active | |
| Butterfly Network | https://job-boards.greenhouse.io/butterflynetwork | greenhouse | active | |
| Elation Health | https://job-boards.greenhouse.io/elationhealth | greenhouse | active | |
| Tebra | https://job-boards.greenhouse.io/tebra | greenhouse | active | |
| Eucalyptus (digital health) | https://job-boards.greenhouse.io/eucalyptus | greenhouse | active | |
| Prosper Health | https://job-boards.greenhouse.io/prosperhealth | greenhouse | active | |
| K Health | https://job-boards.greenhouse.io/khealthcareers | greenhouse | active | |
| Abridge | https://jobs.ashbyhq.com/abridge | ashby | active | |
| Ambience Healthcare | https://jobs.ashbyhq.com/ambiencehealthcare | ashby | active | |
| Headway | https://jobs.ashbyhq.com/headway | ashby | active | |
| Hippocratic AI | https://jobs.ashbyhq.com/Hippocratic%20AI | ashby | active | |
| Notable | https://jobs.ashbyhq.com/notable | ashby | active | |
| Rad AI | https://jobs.ashbyhq.com/radai | ashby | active | |
| Lyra Health | https://jobs.lever.co/lyrahealth | lever | active | |
| Canvas Medical | https://jobs.lever.co/canvasmedical | lever | active | |
| H1 (healthcare data) | https://jobs.lever.co/h1 | lever | active | |
| Glass Health | https://jobs.lever.co/glass-health-inc | lever | active | |
| Providence | https://evac.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Lifepoint Health | https://ibnjjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| IU Health | https://ekcm.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| Atlantic Health System | https://erqh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| Inova | https://elar.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Medtronic | https://medtronic.wd1.myworkdayjobs.com/MedtronicCareers | workday | active | |
| Baxter | https://baxter.wd1.myworkdayjobs.com/baxter | workday | active | |
| Insulet | https://insulet.wd5.myworkdayjobs.com/insuletcareers | workday | active | |
| McKesson | https://mckesson.wd3.myworkdayjobs.com/External_Careers | workday | active | |
| Compassus (Ascension at Home) | https://hospicecom.wd5.myworkdayjobs.com/Compassus | workday | active | |
| Humana | https://humana.wd5.myworkdayjobs.com/Humana_External_Career_Site | workday | active | |
| Elevance Health | https://elevancehealth.wd1.myworkdayjobs.com/ANT | workday | active | |
| CVS Health | https://cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers | workday | active | |
| Centene | https://centene.wd5.myworkdayjobs.com/Centene_External | workday | active | |
| BD (Becton Dickinson) | https://bdx.wd1.myworkdayjobs.com/EXTERNAL_CAREER_SITE_USA | workday | active | |
| Edwards Lifesciences | https://edwards.wd5.myworkdayjobs.com/EdwardsCareers | workday | active | |
| Dexcom | https://dexcom.wd1.myworkdayjobs.com/Dexcom | workday | active | |
| ResMed | https://resmed.wd3.myworkdayjobs.com/ResMed_External_Careers | workday | active | |
| Cardinal Health | https://cardinalhealth.wd1.myworkdayjobs.com/EXT | workday | active | |
| DaVita | https://davita.wd1.myworkdayjobs.com/DKC_External | workday | active | |
| Mass General Brigham | https://massgeneralbrigham.wd1.myworkdayjobs.com/MGBExternal | workday | active | |
| Trinity Health | https://trinityhealth.wd1.myworkdayjobs.com/Jobs | workday | active | |
| Banner Health | https://bannerhealth.wd5.myworkdayjobs.com/Careers | workday | active | |
| Intermountain Health | https://imh.wd108.myworkdayjobs.com/IntermountainCareers | workday | active | |
| Essentia Health | https://essentiahealth.wd1.myworkdayjobs.com/Essentia_Health | workday | active | |

### Media, gaming, consumer, retail & CPG (37/37 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Epic Games | https://job-boards.greenhouse.io/epicgames | greenhouse | active | |
| Scopely | https://job-boards.greenhouse.io/scopely | greenhouse | active | |
| FanDuel | https://job-boards.greenhouse.io/fanduel | greenhouse | active | |
| Sweetgreen | https://job-boards.greenhouse.io/sweetgreen | greenhouse | active | |
| Riot Games | https://job-boards.greenhouse.io/riotgames | greenhouse | active | |
| Twitch | https://job-boards.greenhouse.io/twitch | greenhouse | active | |
| Everlane | https://job-boards.greenhouse.io/everlane | greenhouse | active | |
| Hasbro | https://job-boards.greenhouse.io/hasbro | greenhouse | active | |
| ClassPass | https://job-boards.greenhouse.io/classpass | greenhouse | active | |
| StockX | https://job-boards.greenhouse.io/stockx | greenhouse | active | |
| Take-Two Interactive | https://job-boards.greenhouse.io/taketwo | greenhouse | active | |
| Squarespace | https://job-boards.greenhouse.io/squarespace | greenhouse | active | |
| SeatGeek | https://job-boards.greenhouse.io/seatgeek | greenhouse | active | |
| Glossier | https://job-boards.greenhouse.io/glossier | greenhouse | active | |
| Mindbody | https://job-boards.greenhouse.io/mindbody | greenhouse | active | |
| Thrive Market | https://job-boards.greenhouse.io/thrivemarket | greenhouse | active | |
| Vox Media | https://job-boards.greenhouse.io/voxmedia | greenhouse | active | |
| Wildlife Studios | https://job-boards.greenhouse.io/wildlifestudios | greenhouse | active | |
| Fubo | https://job-boards.greenhouse.io/fubotv | greenhouse | active | |
| OLIPOP | https://job-boards.greenhouse.io/olipop | greenhouse | active | |
| BuzzFeed | https://job-boards.greenhouse.io/buzzfeed | greenhouse | active | |
| Kickstarter | https://job-boards.greenhouse.io/kickstarter | greenhouse | active | |
| Character.AI | https://jobs.ashbyhq.com/character | ashby | active | |
| Substack | https://jobs.ashbyhq.com/substack | ashby | active | |
| Suno | https://jobs.ashbyhq.com/suno | ashby | active | |
| Polymarket | https://jobs.ashbyhq.com/polymarket | ashby | active | |
| Whoop | https://jobs.ashbyhq.com/whoop | ashby | active | |
| Eight Sleep | https://jobs.ashbyhq.com/eightsleep | ashby | active | |
| Kalshi | https://jobs.ashbyhq.com/kalshi | ashby | active | |
| Runway (AI video) | https://jobs.ashbyhq.com/runway | ashby | active | |
| ManyChat | https://jobs.ashbyhq.com/manychat | ashby | active | |
| Kimberly-Clark | https://kimberlyclark.wd1.myworkdayjobs.com/GLOBAL | workday | active | |
| Nordstrom | https://nordstrom.wd501.myworkdayjobs.com/nordstrom_careers | workday | active | |
| Live Nation | https://livenation.wd503.myworkdayjobs.com/LNExternalSite | workday | active | |
| Pepsi Bottling Ventures | https://pbv.wd503.myworkdayjobs.com/external | workday | active | |
| Conagra Brands | https://conagrabrands.wd1.myworkdayjobs.com/Careers_US | workday | active | |
| PVH | https://pvh.wd1.myworkdayjobs.com/PVH_Careers | workday | active | |

### Leftovers from waves 1-2 (empty boards, extra Workday tenants, ambiguous Ashby boards) (28/28 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Unit (banking-as-a-service) | https://jobs.ashbyhq.com/unit | ashby | active | |
| Imprint | https://jobs.ashbyhq.com/imprint | ashby | active | |
| Radiant (Ashby board, company unconfirmed) | https://jobs.ashbyhq.com/radiant | ashby | active | |
| Thinkific | https://job-boards.greenhouse.io/thinkific | greenhouse | active | |
| Learneo (Course Hero) | https://job-boards.greenhouse.io/learneo | greenhouse | active | |
| Vacasa | https://job-boards.greenhouse.io/vacasa | greenhouse | active | |
| Poshmark | https://job-boards.greenhouse.io/poshmark | greenhouse | active | |
| Mercari | https://job-boards.greenhouse.io/mercari | greenhouse | active | |
| Calm | https://job-boards.greenhouse.io/calm | greenhouse | active | |
| Kiddom | https://jobs.lever.co/kiddom | lever | active | |
| Docebo | https://jobs.lever.co/docebo | lever | active | |
| Arcadia Science | https://jobs.lever.co/arcadiascience | lever | active | |
| Outpace Bio | https://jobs.lever.co/outpacebio | lever | active | |
| Brown University Health | https://brownhealth.wd12.myworkdayjobs.com/External_Careers | workday | active | |
| Boston Medical Center | https://bmc.wd1.myworkdayjobs.com/BMC | workday | active | |
| Kansas Health System | https://kansashealthsystem.wd1.myworkdayjobs.com/careers | workday | active | |
| R1 RCM | https://r1rcm.wd1.myworkdayjobs.com/R1RCM | workday | active | |
| Summit Health / CityMD | https://shm.wd5.myworkdayjobs.com/summit_citymd | workday | active | |
| UVM Health Porter Medical Center | https://uvmhealth.wd1.myworkdayjobs.com/Porter | workday | active | |
| Sonora Quest Laboratories | https://bannerhealth.wd108.myworkdayjobs.com/sonoraquestcareers | workday | active | |
| General Mills | https://genmills.wd1.myworkdayjobs.com/GMI_External_Careers | workday | active | |
| Tapestry | https://tapestry.wd108.myworkdayjobs.com/Tapestry_Careers | workday | active | |
| VF Corp | https://vfc.wd5.myworkdayjobs.com/vfc_careers | workday | active | |
| Constellation Brands | https://cbrands.wd5.myworkdayjobs.com/CBI_External_Careers | workday | active | |
| Kohl's | https://kohls.wd504.myworkdayjobs.com/kohlscareers | workday | active | |
| Belk | https://belk.wd1.myworkdayjobs.com/Jobs-Stores | workday | active | |
| BioMarin | https://biomarpeople.wd3.myworkdayjobs.com/BioMar | workday | active | |
| Genentech (Roche) | https://roche.wd3.myworkdayjobs.com/ROG-A2O-GENE | workday | active | |

## Wave 3


### Government, civic tech & non-profit (24/24 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Mozilla | https://job-boards.greenhouse.io/mozilla | greenhouse | active | |
| Wikimedia Foundation | https://job-boards.greenhouse.io/wikimedia | greenhouse | active | |
| Code for America | https://job-boards.greenhouse.io/codeforamerica | greenhouse | active | |
| Code.org | https://job-boards.greenhouse.io/codeorg | greenhouse | active | |
| DonorsChoose | https://job-boards.greenhouse.io/donorschoose | greenhouse | active | |
| Accenture Federal Services | https://job-boards.greenhouse.io/afscareersmarketplace | greenhouse | active | |
| BlackSky | https://job-boards.greenhouse.io/blacksky | greenhouse | active | |
| Chainguard | https://job-boards.greenhouse.io/chainguard | greenhouse | active | |
| Scale AI | https://job-boards.greenhouse.io/scaleai | greenhouse | active | |
| Leidos | https://leidos.wd5.myworkdayjobs.com/External | workday | active | |
| Booz Allen Hamilton | https://bah.wd1.myworkdayjobs.com/BAH_Jobs | workday | active | |
| CACI | https://caci.wd1.myworkdayjobs.com/External | workday | active | |
| Parsons | https://parsons.wd5.myworkdayjobs.com/Search | workday | active | |
| KBR | https://kbr.wd5.myworkdayjobs.com/KBR_Careers | workday | active | |
| MITRE | https://mitre.wd5.myworkdayjobs.com/MITRE | workday | active | |
| Amentum | https://pae.wd1.myworkdayjobs.com/Amentum_Careers | workday | active | |
| Brookhaven National Lab | https://bnl.wd1.myworkdayjobs.com/Externa | workday | active | |
| The Nature Conservancy | https://nature.wd108.myworkdayjobs.com/ExternalCareers | workday | active | |
| Gates Foundation | https://gatesfoundation.wd1.myworkdayjobs.com/Gates | workday | active | |
| Accenture | https://accenture.wd103.myworkdayjobs.com/AccentureCareers | workday | active | |
| American Red Cross | https://americanredcross.wd1.myworkdayjobs.com/American_Red_Cross_Careers | workday | active | |
| GDIT (General Dynamics IT) | https://gdit.wd5.myworkdayjobs.com/External_Career_Site | workday | active | |
| Stanford Health Care | https://stanfordhealthcare.wd5.myworkdayjobs.com/SHC_External_Career_Site | workday | active | |
| MTM (Medicaid transportation) | https://mtminc.wd1.myworkdayjobs.com/MTM_External | workday | active | |

### Enterprise SaaS, dev tools & AI infra (46/46 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Okta | https://job-boards.greenhouse.io/okta | greenhouse | active | |
| Cloudflare | https://job-boards.greenhouse.io/cloudflare | greenhouse | active | |
| Glean | https://job-boards.greenhouse.io/gleanwork | greenhouse | active | |
| Fivetran | https://job-boards.greenhouse.io/fivetran | greenhouse | active | |
| Asana | https://job-boards.greenhouse.io/asana | greenhouse | active | |
| Gusto | https://job-boards.greenhouse.io/gusto | greenhouse | active | |
| Gong | https://job-boards.greenhouse.io/gongio | greenhouse | active | |
| Mixpanel | https://job-boards.greenhouse.io/mixpanel | greenhouse | active | |
| Braze | https://job-boards.greenhouse.io/braze | greenhouse | active | |
| Tailscale | https://job-boards.greenhouse.io/tailscale | greenhouse | active | |
| LaunchDarkly | https://job-boards.greenhouse.io/launchdarkly | greenhouse | active | |
| PagerDuty | https://job-boards.greenhouse.io/pagerduty | greenhouse | active | |
| Fastly | https://job-boards.greenhouse.io/fastly | greenhouse | active | |
| Amplitude | https://job-boards.greenhouse.io/amplitude | greenhouse | active | |
| Salesloft | https://job-boards.greenhouse.io/salesloft | greenhouse | active | |
| Webflow | https://job-boards.greenhouse.io/webflow | greenhouse | active | |
| Cockroach Labs | https://job-boards.greenhouse.io/cockroachlabs | greenhouse | active | |
| Airtable | https://job-boards.greenhouse.io/airtable | greenhouse | active | |
| CircleCI | https://job-boards.greenhouse.io/circleci | greenhouse | active | |
| Sourcegraph | https://job-boards.greenhouse.io/sourcegraph91 | greenhouse | active | |
| Perplexity | https://jobs.ashbyhq.com/perplexity | ashby | active | |
| Modal | https://jobs.ashbyhq.com/modal | ashby | active | |
| Attio | https://jobs.ashbyhq.com/attio | ashby | active | |
| LangChain | https://jobs.ashbyhq.com/langchain | ashby | active | |
| Decagon | https://jobs.ashbyhq.com/decagon | ashby | active | |
| Supabase | https://jobs.ashbyhq.com/supabase | ashby | active | |
| Baseten | https://jobs.ashbyhq.com/baseten | ashby | active | |
| Fireworks AI | https://jobs.ashbyhq.com/fireworks | ashby | active | |
| Harvey | https://jobs.ashbyhq.com/harvey | ashby | active | |
| ElevenLabs | https://jobs.ashbyhq.com/elevenlabs | ashby | active | |
| Replit | https://jobs.ashbyhq.com/replit | ashby | active | |
| Cohere | https://jobs.ashbyhq.com/cohere | ashby | active | |
| Vanta | https://jobs.ashbyhq.com/vanta | ashby | active | |
| Sierra (AI agents) | https://jobs.ashbyhq.com/sierra | ashby | active | |
| PostHog | https://jobs.ashbyhq.com/posthog | ashby | active | |
| Hex | https://jobs.ashbyhq.com/hex | ashby | active | |
| Cursor (flag for review) | https://jobs.ashbyhq.com/cursor | ashby | active | |
| Pinecone | https://jobs.ashbyhq.com/pinecone | ashby | active | |
| Outreach | https://jobs.lever.co/outreach | lever | active | |
| CrowdStrike | https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers | workday | active | |
| Salesforce | https://salesforce.wd12.myworkdayjobs.com/External_Career_Site | workday | active | |
| Adobe | https://adobe.wd5.myworkdayjobs.com/external_experienced | workday | active | |
| Workday, Inc. | https://workday.wd5.myworkdayjobs.com/Workday | workday | active | |
| ThoughtSpot | https://thoughtspot.wd5.myworkdayjobs.com/careers | workday | active | |
| Zoom | https://zoom.wd5.myworkdayjobs.com/Zoom | workday | active | |
| Intapp | https://intapp.wd1.myworkdayjobs.com/intapp | workday | active | |

### Insurance carriers & cybersecurity (32/32 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Zscaler | https://job-boards.greenhouse.io/zscaler | greenhouse | active | |
| Wiz | https://job-boards.greenhouse.io/wizinc | greenhouse | active | |
| Tanium | https://job-boards.greenhouse.io/tanium | greenhouse | active | |
| Cribl | https://job-boards.greenhouse.io/cribl | greenhouse | active | |
| Axonius | https://job-boards.greenhouse.io/axonius | greenhouse | active | |
| Expel | https://job-boards.greenhouse.io/expel | greenhouse | active | |
| 1Password | https://jobs.ashbyhq.com/1password | ashby | active | |
| Socket (security) | https://jobs.ashbyhq.com/socket | ashby | active | |
| Chubb | https://fa-ewgu-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/jobs | oracle_fusion | active | |
| Allstate | https://allstate.wd5.myworkdayjobs.com/allstate_careers | workday | active | |
| AIG | https://aig.wd1.myworkdayjobs.com/aig | workday | active | |
| The Hartford | https://thehartford.wd5.myworkdayjobs.com/Careers_External | workday | active | |
| Marsh McLennan | https://mmc.wd1.myworkdayjobs.com/MMC | workday | active | |
| MassMutual | https://massmutual.wd1.myworkdayjobs.com/MMAscendCareers | workday | active | |
| Guardian Life | https://guardianlife.wd5.myworkdayjobs.com/Guardian-Life-Careers | workday | active | |
| Unum | https://unum.wd1.myworkdayjobs.com/External | workday | active | |
| Assurant | https://assurant.wd1.myworkdayjobs.com/Assurant_Careers | workday | active | |
| Voya Financial | https://godirect.wd5.myworkdayjobs.com/voya_jobs | workday | active | |
| Ameriprise | https://ameriprise.wd5.myworkdayjobs.com/Ameriprise | workday | active | |
| Everest Re | https://everestre.wd5.myworkdayjobs.com/careers | workday | active | |
| Tokio Marine HCC | https://tmhcc.wd108.myworkdayjobs.com/External | workday | active | |
| Athene (Apollo) | https://athene.wd5.myworkdayjobs.com/Apollo_Careers | workday | active | |
| Proofpoint | https://proofpoint.wd5.myworkdayjobs.com/ProofpointCareers | workday | active | |
| SailPoint | https://sailpoint.wd1.myworkdayjobs.com/SailPoint | workday | active | |
| Rapid7 | https://mymoose.wd1.myworkdayjobs.com/careers | workday | active | |
| Great American Insurance Group | https://gaig.wd1.myworkdayjobs.com/GAIG_External | workday | active | |
| Brown & Brown | https://bbinsurance.wd1.myworkdayjobs.com/Careers | workday | active | |
| Auto-Owners | https://aoins.wd5.myworkdayjobs.com/AutoOwners | workday | active | |
| MSIG North America | https://msigna.wd5.myworkdayjobs.com/gbl | workday | active | |
| Highmark Health | https://highmarkhealth.wd1.myworkdayjobs.com/highmark | workday | active | |
| FWD | https://fwd.wd3.myworkdayjobs.com/FWDcareersite | workday | active | |
| Swiss Life International | https://swisslife.wd3.myworkdayjobs.com/en-US/Swiss_Life_International_Division_Career_Site | workday | active | |

### Staffing, legal, consulting & HR-tech (47/47 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Everlaw | https://job-boards.greenhouse.io/everlaw | greenhouse | active | |
| Remote (HR platform) | https://job-boards.greenhouse.io/remotecom | greenhouse | active | |
| Justworks | https://job-boards.greenhouse.io/justworks | greenhouse | active | |
| Checkr | https://job-boards.greenhouse.io/checkr | greenhouse | active | |
| Thoughtworks | https://job-boards.greenhouse.io/thoughtworks | greenhouse | active | |
| Culture Amp | https://job-boards.greenhouse.io/cultureamp | greenhouse | active | |
| Upwork | https://job-boards.greenhouse.io/upwork | greenhouse | active | |
| Xebia (CEE) | https://job-boards.greenhouse.io/xebiacee | greenhouse | active | |
| TaxRise | https://job-boards.greenhouse.io/taxrise | greenhouse | active | |
| Legora | https://jobs.ashbyhq.com/legora | ashby | active | |
| Metaview | https://jobs.ashbyhq.com/metaview | ashby | active | |
| Juicebox | https://jobs.ashbyhq.com/juicebox | ashby | active | |
| Ashby (recruiting software) | https://jobs.ashbyhq.com/ashby | ashby | active | |
| Deel | https://jobs.ashbyhq.com/deel | ashby | active | |
| AHEAD | https://jobs.lever.co/thinkahead | lever | active | |
| BPM LLP | https://jobs.lever.co/bpmcpa | lever | active | |
| Jobgether | https://jobs.lever.co/jobgether | lever | active | |
| KPMG (Careers) | https://elzw.fa.em8.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| KPMG Global Services | https://ejgk.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_3/jobs | oracle_fusion | active | |
| WTW (Willis Towers Watson) | https://eedu.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1003/jobs | oracle_fusion | active | |
| Grant Thornton (US) | https://ehzq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Gartner | https://gartner.wd5.myworkdayjobs.com/EXT | workday | active | |
| Huron | https://huron.wd1.myworkdayjobs.com/huroncareers | workday | active | |
| Randstad (US) | https://randstad.wd502.myworkdayjobs.com/randstadcareers | workday | active | |
| Guidehouse | https://guidehouse.wd1.myworkdayjobs.com/External | workday | active | |
| ICF | https://icf.wd5.myworkdayjobs.com/ICFExternal_Career_Site | workday | active | |
| Concentrix | https://cnx.wd1.myworkdayjobs.com/external_global | workday | active | |
| Genpact | https://genpact.wd108.myworkdayjobs.com/External_Careers | workday | active | |
| PwC (US) | https://pwc.wd3.myworkdayjobs.com/US_Experienced_Careers | workday | active | |
| Thomson Reuters | https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site | workday | active | |
| LexisNexis (RELX) | https://relx.wd3.myworkdayjobs.com/LexisNexisLegal | workday | active | |
| Wolters Kluwer | https://wk.wd3.myworkdayjobs.com/External | workday | active | |
| Robert Half | https://roberthalf.wd1.myworkdayjobs.com/RobertHalfStaffingCareers | workday | active | |
| Protiviti | https://roberthalf.wd1.myworkdayjobs.com/ProtivitiExperiencedCareers | workday | active | |
| Alvarez & Marsal | https://alvarezandmarsal.wd1.myworkdayjobs.com/alvarezandmarsalp | workday | active | |
| FTI Consulting | https://fticonsulting.wd108.myworkdayjobs.com/FTIConsultingCareers | workday | active | |
| Crowe | https://crowe.wd12.myworkdayjobs.com/External_Careers | workday | active | |
| RSM | https://rsm.wd1.myworkdayjobs.com/RSMCareers | workday | active | |
| CliftonLarsonAllen (CLA) | https://cliftonlarsonallen.wd115.myworkdayjobs.com/CLA | workday | active | |
| AMN Healthcare | https://amn.wd1.myworkdayjobs.com/AMN_Careers | workday | active | |
| Kyndryl | https://kyndryl.wd5.myworkdayjobs.com/KyndrylProfessionalCareers | workday | active | |
| OneDigital | https://onedigital.wd5.myworkdayjobs.com/OneDigital | workday | active | |
| Omnicom | https://interpublic.wd5.myworkdayjobs.com/OMC | workday | active | |
| Unisys | https://unisys.wd5.myworkdayjobs.com/External | workday | active | |
| SLR Consulting | https://slrconsulting.wd103.myworkdayjobs.com/SLRCareers | workday | active | |
| Willis Re | https://willisre.wd107.myworkdayjobs.com/Careers | workday | active | |
| Cognizant | https://mycts.wd1.myworkdayjobs.com/CTS | workday | active | |

### Travel, telecom, automotive & agriculture/food (60/60 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Carvana | https://job-boards.greenhouse.io/carvana | greenhouse | active | |
| HelloFresh | https://job-boards.greenhouse.io/hellofresh | greenhouse | active | |
| AST SpaceMobile | https://job-boards.greenhouse.io/astspacemobile | greenhouse | active | |
| Kodiak Robotics | https://job-boards.greenhouse.io/kodiak | greenhouse | active | |
| Lyft | https://job-boards.greenhouse.io/lyft | greenhouse | active | |
| Via | https://job-boards.greenhouse.io/via | greenhouse | active | |
| Ripple | https://job-boards.greenhouse.io/ripple | greenhouse | active | |
| OpenTable | https://job-boards.greenhouse.io/opentable | greenhouse | active | |
| Tripadvisor | https://job-boards.greenhouse.io/tripadvisor | greenhouse | active | |
| Misfits Market | https://job-boards.greenhouse.io/misfitsmarket | greenhouse | active | |
| Dialpad | https://job-boards.greenhouse.io/dialpad | greenhouse | active | |
| Faraday Future | https://job-boards.greenhouse.io/faradayfuture | greenhouse | active | |
| GetYourGuide | https://job-boards.greenhouse.io/getyourguide | greenhouse | active | |
| Cloudbeds | https://job-boards.greenhouse.io/cloudbeds | greenhouse | active | |
| CarGurus | https://job-boards.greenhouse.io/cargurus | greenhouse | active | |
| Nextiva | https://job-boards.greenhouse.io/nextiva | greenhouse | active | |
| Bird | https://job-boards.greenhouse.io/bird | greenhouse | active | |
| Bandwidth | https://job-boards.greenhouse.io/bandwidth | greenhouse | active | |
| Vonage | https://job-boards.greenhouse.io/vonage | greenhouse | active | |
| Carbon Robotics | https://job-boards.greenhouse.io/carbonrobotics | greenhouse | active | |
| Blue River Technology | https://job-boards.greenhouse.io/bluerivertech | greenhouse | active | |
| Ooma | https://job-boards.greenhouse.io/ooma | greenhouse | active | |
| Stack AV | https://job-boards.greenhouse.io/stackav | greenhouse | active | |
| Solid Power | https://job-boards.greenhouse.io/solidpower | greenhouse | active | |
| Vital Farms | https://job-boards.greenhouse.io/vitalfarms | greenhouse | active | |
| Hungryroot | https://job-boards.greenhouse.io/hungryroot | greenhouse | active | |
| Liquid Death | https://job-boards.greenhouse.io/liquiddeath | greenhouse | active | |
| Gopuff | https://jobs.lever.co/gopuff | lever | active | |
| Waabi | https://jobs.lever.co/waabi | lever | active | |
| Aeva Technologies | https://jobs.lever.co/aeva | lever | active | |
| Olo | https://jobs.lever.co/olo | lever | active | |
| Skydio | https://jobs.ashbyhq.com/skydio | ashby | active | |
| KAYAK | https://jobs.ashbyhq.com/kayak | ashby | active | |
| Hopper | https://jobs.ashbyhq.com/hopper | ashby | active | |
| Wayve | https://jobs.ashbyhq.com/wayve | ashby | active | |
| T-Mobile | https://tmobile.wd1.myworkdayjobs.com/External | workday | active | |
| Sysco | https://sysco.wd5.myworkdayjobs.com/syscocareers | workday | active | |
| Tyson Foods | https://tysonfoods.wd5.myworkdayjobs.com/TSN | workday | active | |
| Chipotle | https://chipotle.wd5.myworkdayjobs.com/ChipotleCareers | workday | active | |
| Norwegian Cruise Line Holdings | https://nclh.wd108.myworkdayjobs.com/NCLH_Careers | workday | active | |
| Choice Hotels | https://choicehotels.wd5.myworkdayjobs.com/External | workday | active | |
| Travel + Leisure Co. (Wyndham) | https://wynd.wd5.myworkdayjobs.com/External | workday | active | |
| Avis Budget Group | https://avisbudget.wd1.myworkdayjobs.com/ABG_Careers | workday | active | |
| Marriott Vacations Worldwide | https://mymvw.wd5.myworkdayjobs.com/MVW | workday | active | |
| HEI Hotels & Resorts | https://heihotels.wd12.myworkdayjobs.com/External_Career_Site | workday | active | |
| HRI Hospitality | https://hriproperties.wd12.myworkdayjobs.com/HRI_Hospitality | workday | active | |
| Benchmark Hospitality | https://benchmark.wd1.myworkdayjobs.com/PGH_Careers | workday | active | |
| Breeze Airways | https://flybreeze.wd503.myworkdayjobs.com/Breeze_Airways | workday | active | |
| Republic Airways | https://rjet.wd5.myworkdayjobs.com/External_Career_Site | workday | active | |
| Piedmont Airlines | https://aaregional.wd503.myworkdayjobs.com/Search | workday | active | |
| SeaWorld Entertainment | https://seaworldentertainment.wd1.myworkdayjobs.com/SEA | workday | active | |
| Disney | https://disney.wd5.myworkdayjobs.com/disneycareer | workday | active | |
| Aptiv | https://aptiv.wd5.myworkdayjobs.com/APTIV_CAREERS | workday | active | |
| BorgWarner | https://borgwarner.wd5.myworkdayjobs.com/BorgWarner_Careers | workday | active | |
| Goodyear | https://goodyear.wd1.myworkdayjobs.com/GoodyearCareers | workday | active | |
| Adient | https://adient.wd3.myworkdayjobs.com/External | workday | active | |
| Corteva | https://corteva.wd5.myworkdayjobs.com/Corteva | workday | active | |
| Speedcast | https://speedcast.wd1.myworkdayjobs.com/speedcastcareers | workday | active | |
| Lumentum | https://lumentum.wd5.myworkdayjobs.com/LITE | workday | active | |
| Viterra (Bunge) | https://viterra.wd3.myworkdayjobs.com/External-CAN | workday | active | |

## Wave 4


### European/international SMBs on Personio, Recruitee & BreezyHR (first boards on these three adapters) (102/102 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Westwing | https://westwing.jobs.personio.de/ | personio | active | |
| Holidu | https://holidu.jobs.personio.de/ | personio | active | |
| statworx | https://statworx.jobs.personio.de/ | personio | active | |
| 360T | https://360t.jobs.personio.de/ | personio | active | |
| Vivid Money | https://vivid.jobs.personio.de/ | personio | active | |
| Alexander Thamm | https://alexander-thamm-gmbh.jobs.personio.de/ | personio | active | |
| XITASO | https://xitaso.jobs.personio.de/ | personio | active | |
| Mawave Marketing | https://mawave-marketing-gmbh.jobs.personio.de/ | personio | active | |
| Mercanis | https://mercanis.jobs.personio.de/ | personio | active | |
| tonies | https://tonies.jobs.personio.de/ | personio | active | |
| Checkmk | https://checkmk-gmbh.jobs.personio.de/ | personio | active | |
| KNIME | https://knime.jobs.personio.de/ | personio | active | |
| Wunderflats | https://wunderflats.jobs.personio.de/ | personio | active | |
| Capmo | https://capmo.jobs.personio.de/ | personio | active | |
| ottonova | https://ottonova.jobs.personio.de/ | personio | active | |
| Highberg | https://schickler.jobs.personio.de/ | personio | active | |
| Tanso | https://tanso.jobs.personio.de/ | personio | active | |
| Lumenaza | https://lumenaza.jobs.personio.de/ | personio | active | |
| Maltego | https://maltego.jobs.personio.de/ | personio | active | |
| nerdware | https://nerdware.jobs.personio.de/ | personio | active | |
| baramundi | https://baramundi-software-ag.jobs.personio.de/ | personio | active | |
| YOUKI | https://youki-gmbh.jobs.personio.de/ | personio | active | |
| gridX | https://gridx.jobs.personio.de/ | personio | active | |
| Hypatos | https://hypatos-gmbh.jobs.personio.de/ | personio | active | |
| Predium | https://predium.jobs.personio.de/ | personio | active | |
| CodeCamp:N | https://codecampn.jobs.personio.de/ | personio | active | |
| IPO Solutions | https://ipo.jobs.personio.de/ | personio | active | |
| Langdock | https://langdock.jobs.personio.de/ | personio | active | |
| Parqet | https://parqet.jobs.personio.de/ | personio | active | |
| Choco | https://choco.jobs.personio.de/ | personio | active | |
| abcfinlab | https://abcfinlab.jobs.personio.de/ | personio | active | |
| advidera | https://advidera-gmbh-co-kg.jobs.personio.de/ | personio | active | |
| Finway | https://finway.jobs.personio.de/ | personio | active | |
| GET.ON | https://geton.jobs.personio.de/ | personio | active | |
| Humanoo | https://humanoo.jobs.personio.de/ | personio | active | |
| MeisterLabs | https://meister.jobs.personio.de/ | personio | active | |
| Mobimeo | https://mobimeo.jobs.personio.de/ | personio | active | |
| mediaire | https://Mediaire.jobs.personio.de/ | personio | active | |
| Personio | https://personio.jobs.personio.de/ | personio | active | |
| Personio (personio-gmbh board, ownership unclear) | https://personio-gmbh.jobs.personio.de/ | personio | active | |
| Deerns | https://jobsdeerns.recruitee.com/ | recruitee | active | |
| Aikido Security | https://aikidosecurity.recruitee.com/ | recruitee | active | |
| Metyis | https://metyisag.recruitee.com/ | recruitee | active | |
| Trafilea | https://trafilea.recruitee.com/ | recruitee | active | |
| Fastned | https://fastned.recruitee.com/ | recruitee | active | |
| Lomography | https://lomography.recruitee.com/ | recruitee | active | |
| Great Minds | https://greatminds.recruitee.com/ | recruitee | active | |
| Optics11 | https://optics11.recruitee.com/ | recruitee | active | |
| Envipco | https://envipco.recruitee.com/ | recruitee | active | |
| Entyre | https://entyreinc.recruitee.com/ | recruitee | active | |
| SkyCell | https://skycellag.recruitee.com/ | recruitee | active | |
| Addepto | https://addepto.recruitee.com/ | recruitee | active | |
| GRID eSports | https://grid.recruitee.com/ | recruitee | active | |
| celebrate company | https://celebratecompany.recruitee.com/ | recruitee | active | |
| Riverflex | https://riverflex.recruitee.com/ | recruitee | active | |
| Hostaway | https://hostaway.recruitee.com/ | recruitee | active | |
| AlmavivA de Belgique | https://almavivadebelgique.recruitee.com/ | recruitee | active | |
| Tidio | https://tidiocareer.recruitee.com/ | recruitee | active | |
| Kodify | https://kodify.recruitee.com/ | recruitee | active | |
| Wordbank | https://wordbank.recruitee.com/ | recruitee | active | |
| Atheneum Partners | https://atheneum.recruitee.com/ | recruitee | active | |
| BridgeFund | https://bridgefund.recruitee.com/ | recruitee | active | |
| NEM Energy | https://nemenergy.recruitee.com/ | recruitee | active | |
| SidelineSwap | https://sidelineswap.recruitee.com/ | recruitee | active | |
| Time Doctor | https://timedoctor.recruitee.com/ | recruitee | active | |
| Tiugo | https://tiugotech.recruitee.com/ | recruitee | active | |
| WoodWing | https://woodwing.recruitee.com/ | recruitee | active | |
| Eneve | https://eneve.recruitee.com/ | recruitee | active | |
| Ferryscanner | https://ferryscanner.recruitee.com/ | recruitee | active | |
| Hygraph | https://hygraph.recruitee.com/ | recruitee | active | |
| Amilia | https://amilia.recruitee.com/ | recruitee | active | |
| Skytree | https://skytree.recruitee.com/ | recruitee | active | |
| American Logistics Authority | https://american-logistics-authority.breezy.hr/ | breezyhr | active | |
| Surge Staffing | https://surge.breezy.hr/ | breezyhr | active | |
| Embraer | https://embraer.breezy.hr/ | breezyhr | active | |
| Urrly | https://urrly.breezy.hr/ | breezyhr | active | |
| Atlas Technica | https://atlas-technica.breezy.hr/ | breezyhr | active | |
| LufCo | https://lufco.breezy.hr/ | breezyhr | active | |
| Seasats | https://seasats.breezy.hr/ | breezyhr | active | |
| A2H | https://a2h.breezy.hr/ | breezyhr | active | |
| Aimpoint Digital | https://aimpoint-digital.breezy.hr/ | breezyhr | active | |
| TXP | https://vigil-global.breezy.hr/ | breezyhr | active | |
| gritmind | https://gritmind.breezy.hr/ | breezyhr | active | |
| Norbert Health | https://norbert-health.breezy.hr/ | breezyhr | active | |
| Vagaro | https://vagaro.breezy.hr/ | breezyhr | active | |
| Throne Labs | https://thronelabs.breezy.hr/ | breezyhr | active | |
| Karen Clark & Company | https://karen-clark-company.breezy.hr/ | breezyhr | active | |
| PrimaryMD | https://primarymd.breezy.hr/ | breezyhr | active | |
| Totara Learning Solutions | https://totara-learning-solutions.breezy.hr/ | breezyhr | active | |
| Clarity RCM | https://clarity-rcm.breezy.hr/ | breezyhr | active | |
| Clever Real Estate | https://clever-real-estate.breezy.hr/ | breezyhr | active | |
| Manara | https://manara.breezy.hr/ | breezyhr | active | |
| Ceno Group | https://ceno-group-inc.breezy.hr/ | breezyhr | active | |
| Opterus | https://opterus.breezy.hr/ | breezyhr | active | |
| NurseDash | https://nursedash.breezy.hr/ | breezyhr | active | |
| Aiden Technologies | https://aiden-technologies-inc.breezy.hr/ | breezyhr | active | |
| Continued | https://continued.breezy.hr/ | breezyhr | active | |
| Driver | https://driver-ai-inc.breezy.hr/ | breezyhr | active | |
| Edfinity | https://edfinity.breezy.hr/ | breezyhr | active | |
| Entermotion | https://entermotion.breezy.hr/ | breezyhr | active | |
| Falkonry | https://falkonry.breezy.hr/ | breezyhr | active | |
| Renalogic | https://renalogic.breezy.hr/ | breezyhr | active | |

### AI-native, dev tools, infra, security & robotics (Ashby + Lever) (104/104 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Pika | https://jobs.ashbyhq.com/pika | ashby | active | |
| Writer (generative AI) | https://jobs.ashbyhq.com/writer | ashby | active | |
| Synthesia | https://jobs.ashbyhq.com/synthesia | ashby | active | |
| Anyscale | https://jobs.ashbyhq.com/anyscale | ashby | active | |
| Poolside | https://jobs.ashbyhq.com/poolside | ashby | active | |
| Reka | https://jobs.ashbyhq.com/reka | ashby | active | |
| Physical Intelligence | https://jobs.ashbyhq.com/physicalintelligence | ashby | active | |
| 1X Technologies | https://jobs.ashbyhq.com/1x | ashby | active | |
| OSARO | https://jobs.lever.co/osaro | lever | active | |
| Encord | https://jobs.ashbyhq.com/encord | ashby | active | |
| Roboflow | https://jobs.ashbyhq.com/roboflow | ashby | active | |
| Weaviate | https://jobs.ashbyhq.com/weaviate | ashby | active | |
| Zilliz | https://jobs.lever.co/zilliz | lever | active | |
| turbopuffer | https://jobs.ashbyhq.com/turbopuffer | ashby | active | |
| Runpod | https://jobs.ashbyhq.com/runpod | ashby | active | |
| Render (cloud platform) | https://jobs.ashbyhq.com/render | ashby | active | |
| Railway (dev platform) | https://jobs.ashbyhq.com/railway | ashby | active | |
| Prefect | https://jobs.ashbyhq.com/prefect | ashby | active | |
| Warp (terminal) | https://jobs.ashbyhq.com/warp | ashby | active | |
| Zed Industries | https://jobs.ashbyhq.com/zed | ashby | active | |
| Graphite (code review) | https://jobs.ashbyhq.com/graphite | ashby | active | |
| Lovable | https://jobs.ashbyhq.com/lovable | ashby | active | |
| Sentry | https://jobs.ashbyhq.com/sentry | ashby | active | |
| Mux | https://jobs.ashbyhq.com/mux | ashby | active | |
| LiveKit | https://jobs.ashbyhq.com/livekit | ashby | active | |
| Stream (chat/feeds SDK) | https://jobs.ashbyhq.com/stream | ashby | active | |
| Clerk (auth) | https://jobs.ashbyhq.com/clerk | ashby | active | |
| WorkOS | https://jobs.ashbyhq.com/workos | ashby | active | |
| Inngest | https://jobs.ashbyhq.com/inngest | ashby | active | |
| Sonatype | https://jobs.lever.co/sonatype | lever | active | |
| Semgrep | https://jobs.ashbyhq.com/semgrep | ashby | active | |
| Secureframe | https://jobs.ashbyhq.com/secureframe | ashby | active | |
| Secureframe (Lever board) | https://jobs.lever.co/secureframe | lever | active | |
| Oneleet | https://jobs.ashbyhq.com/oneleet | ashby | active | |
| Midjourney | https://jobs.ashbyhq.com/midjourney | ashby | active | |
| Cartesia | https://jobs.ashbyhq.com/cartesia | ashby | active | |
| Sesame | https://jobs.ashbyhq.com/sesame | ashby | active | |
| Ideogram | https://jobs.ashbyhq.com/ideogram | ashby | active | |
| Krea | https://jobs.ashbyhq.com/krea | ashby | active | |
| Genmo | https://jobs.ashbyhq.com/genmo | ashby | active | |
| Cognition (Devin) | https://jobs.ashbyhq.com/cognition | ashby | active | |
| Factory (AI coding) | https://jobs.ashbyhq.com/factory | ashby | active | |
| Parloa | https://jobs.ashbyhq.com/parloa | ashby | active | |
| OpenEvidence | https://jobs.ashbyhq.com/openevidence | ashby | active | |
| Tennr | https://jobs.ashbyhq.com/tennr | ashby | active | |
| TensorWave | https://jobs.ashbyhq.com/tensorwave | ashby | active | |
| Hyperbolic Labs | https://jobs.ashbyhq.com/hyperbolic | ashby | active | |
| SF Compute | https://jobs.ashbyhq.com/sfcompute | ashby | active | |
| Thinking Machines Lab | https://jobs.ashbyhq.com/thinkingmachines | ashby | active | |
| Reflection AI | https://jobs.ashbyhq.com/reflectionai | ashby | active | |
| Harmonic (math AI) | https://jobs.ashbyhq.com/harmonic | ashby | active | |
| Mercor | https://jobs.ashbyhq.com/mercor | ashby | active | |
| Agtonomy | https://jobs.lever.co/agtonomy | lever | active | |
| ANYbotics | https://jobs.lever.co/anybotics | lever | active | |
| Humanoid (UK robotics) | https://jobs.ashbyhq.com/humanoid | ashby | active | |
| Genesis AI (robotics) | https://jobs.ashbyhq.com/genesis | ashby | active | |
| Cloudinary | https://jobs.lever.co/cloudinary | lever | active | |
| Zapier | https://jobs.ashbyhq.com/zapier | ashby | active | |
| n8n | https://jobs.ashbyhq.com/n8n | ashby | active | |
| Materialize | https://jobs.ashbyhq.com/materialize | ashby | active | |
| MotherDuck | https://jobs.ashbyhq.com/motherduck | ashby | active | |
| Hightouch | https://jobs.ashbyhq.com/hightouch | ashby | active | |
| Fullstory | https://jobs.ashbyhq.com/fullstory | ashby | active | |
| LogRocket | https://jobs.lever.co/logrocket | lever | active | |
| incident.io | https://jobs.ashbyhq.com/incident | ashby | active | |
| OpsLevel | https://jobs.ashbyhq.com/opslevel | ashby | active | |
| Depot (build acceleration) | https://jobs.ashbyhq.com/depot | ashby | active | |
| Coder (dev environments) | https://jobs.ashbyhq.com/coder | ashby | active | |
| Sentra (data security) | https://jobs.ashbyhq.com/sentra | ashby | active | |
| Hoxhunt | https://jobs.ashbyhq.com/hoxhunt | ashby | active | |
| Neon (game payments) | https://jobs.ashbyhq.com/neon | ashby | active | |
| Oligo (manufacturing AI) | https://jobs.ashbyhq.com/oligo | ashby | active | |
| Lightning Labs | https://jobs.ashbyhq.com/lightning | ashby | active | |
| Orca (Solana DEX) | https://jobs.ashbyhq.com/orca | ashby | active | |
| Browserbase | https://jobs.ashbyhq.com/browserbase | ashby | active | |
| E2B | https://jobs.ashbyhq.com/e2b | ashby | active | |
| Braintrust (AI observability) | https://jobs.ashbyhq.com/braintrust | ashby | active | |
| Relevance AI | https://jobs.ashbyhq.com/relevanceai | ashby | active | |
| Dust (AI agents) | https://jobs.ashbyhq.com/dust | ashby | active | |
| Unstructured | https://jobs.ashbyhq.com/unstructured | ashby | active | |
| LlamaIndex | https://jobs.ashbyhq.com/llamaindex | ashby | active | |
| Letta | https://jobs.ashbyhq.com/letta | ashby | active | |
| Mem0 | https://jobs.ashbyhq.com/mem0 | ashby | active | |
| Resend | https://jobs.ashbyhq.com/resend | ashby | active | |
| Knock (notifications) | https://jobs.ashbyhq.com/knock | ashby | active | |
| Svix | https://jobs.ashbyhq.com/svix | ashby | active | |
| Speakeasy | https://jobs.ashbyhq.com/speakeasy | ashby | active | |
| Mintlify | https://jobs.ashbyhq.com/mintlify | ashby | active | |
| GitBook | https://jobs.ashbyhq.com/gitbook | ashby | active | |
| Kong | https://jobs.ashbyhq.com/kong | ashby | active | |
| Cyberhaven | https://jobs.ashbyhq.com/cyberhaven | ashby | active | |
| ClickHouse | https://jobs.ashbyhq.com/clickhouse | ashby | active | |
| Snowflake (Ashby board) | https://jobs.ashbyhq.com/snowflake | ashby | active | |
| Confluent | https://jobs.ashbyhq.com/confluent | ashby | active | |
| Axiom (zero-knowledge) | https://jobs.ashbyhq.com/axiom | ashby | active | |
| Checkly | https://jobs.ashbyhq.com/checkly | ashby | active | |
| Ghost (AI agents) | https://jobs.ashbyhq.com/ghost | ashby | active | |
| Sanity | https://jobs.ashbyhq.com/sanity | ashby | active | |
| Miro | https://jobs.ashbyhq.com/miro | ashby | active | |
| ClickUp | https://jobs.ashbyhq.com/clickup | ashby | active | |
| Vultr | https://jobs.ashbyhq.com/vultr | ashby | active | |
| Scaleway | https://jobs.lever.co/scaleway | lever | active | |
| Unify (GTM) | https://jobs.ashbyhq.com/unify | ashby | active | |
| Demandbase | https://jobs.ashbyhq.com/demandbase | ashby | active | |

### Fintech, crypto, digital health, biotech, climate & consumer (Ashby + Lever) (89/89 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Airwallex | https://jobs.ashbyhq.com/airwallex | ashby | active | |
| Nubank | https://jobs.ashbyhq.com/nubank | ashby | active | |
| Pennylane | https://jobs.ashbyhq.com/pennylane | ashby | active | |
| Alan | https://jobs.ashbyhq.com/alan | ashby | active | |
| Lendable | https://jobs.ashbyhq.com/lendable | ashby | active | |
| Qonto | https://jobs.ashbyhq.com/qonto | ashby | active | |
| Mollie | https://jobs.ashbyhq.com/mollie | ashby | active | |
| Lemonade | https://jobs.ashbyhq.com/lemonade | ashby | active | |
| Elliptic | https://jobs.ashbyhq.com/elliptic | ashby | active | |
| Bestow | https://jobs.ashbyhq.com/bestow | ashby | active | |
| Alchemy | https://jobs.ashbyhq.com/alchemy | ashby | active | |
| Oscilar | https://jobs.ashbyhq.com/oscilar | ashby | active | |
| Mesh | https://jobs.ashbyhq.com/mesh | ashby | active | |
| Kin Insurance | https://jobs.ashbyhq.com/kin | ashby | active | |
| Paxos | https://jobs.ashbyhq.com/paxos | ashby | active | |
| Phantom | https://jobs.ashbyhq.com/phantom | ashby | active | |
| Novo | https://jobs.ashbyhq.com/novo | ashby | active | |
| Astra Financial | https://jobs.ashbyhq.com/astra | ashby | active | |
| Marshmallow | https://jobs.ashbyhq.com/marshmallow | ashby | active | |
| Acorns | https://jobs.ashbyhq.com/acorns | ashby | active | |
| Modern Treasury | https://jobs.ashbyhq.com/moderntreasury | ashby | active | |
| Uniswap Labs | https://jobs.ashbyhq.com/uniswap | ashby | active | |
| Zilch | https://jobs.ashbyhq.com/zilch | ashby | active | |
| Velocity | https://jobs.ashbyhq.com/velocity | ashby | active | |
| Ledger | https://jobs.ashbyhq.com/ledger | ashby | active | |
| Sky Mavis | https://jobs.ashbyhq.com/skymavis | ashby | active | |
| Capchase | https://jobs.ashbyhq.com/capchase | ashby | active | |
| Magic Eden | https://jobs.ashbyhq.com/magiceden | ashby | active | |
| Meow | https://jobs.ashbyhq.com/meow | ashby | active | |
| Found | https://jobs.ashbyhq.com/found | ashby | active | |
| Mysten Labs | https://jobs.ashbyhq.com/mystenlabs | ashby | active | |
| Openly | https://jobs.ashbyhq.com/openly | ashby | active | |
| Clearco | https://jobs.ashbyhq.com/clearco | ashby | active | |
| OpenSea | https://jobs.ashbyhq.com/opensea | ashby | active | |
| Caribou (international tax) | https://jobs.ashbyhq.com/caribou | ashby | active | |
| Mosaic (deal modeling) | https://jobs.ashbyhq.com/mosaic | ashby | active | |
| Talkiatry | https://jobs.ashbyhq.com/talkiatry | ashby | active | |
| Benchling | https://jobs.ashbyhq.com/benchling | ashby | active | |
| Rula Health | https://jobs.ashbyhq.com/rula | ashby | active | |
| Leap (specialty pharmacy benefits) | https://jobs.ashbyhq.com/leap | ashby | active | |
| insitro | https://jobs.ashbyhq.com/insitro | ashby | active | |
| Lunar (health-system software, flag for review) | https://jobs.ashbyhq.com/lunar | ashby | active | |
| Akasa | https://jobs.ashbyhq.com/akasa | ashby | active | |
| Latent Labs | https://jobs.ashbyhq.com/latentlabs | ashby | active | |
| Basecamp Research | https://jobs.ashbyhq.com/basecamp-research | ashby | active | |
| Wheel | https://jobs.ashbyhq.com/wheel | ashby | active | |
| ZOE | https://jobs.ashbyhq.com/zoe | ashby | active | |
| Firsthand | https://jobs.ashbyhq.com/firsthand | ashby | active | |
| Form Energy | https://jobs.ashbyhq.com/formenergy | ashby | active | |
| Base Power | https://jobs.ashbyhq.com/base-power | ashby | active | |
| Helion Energy | https://jobs.ashbyhq.com/helion | ashby | active | |
| Span | https://jobs.ashbyhq.com/span | ashby | active | |
| Watershed | https://jobs.ashbyhq.com/watershed | ashby | active | |
| Generate Capital | https://jobs.ashbyhq.com/generate | ashby | active | |
| Aurora Solar | https://jobs.ashbyhq.com/aurorasolar | ashby | active | |
| Sylvera | https://jobs.ashbyhq.com/sylvera | ashby | active | |
| Twelve | https://jobs.ashbyhq.com/twelve | ashby | active | |
| Rothy's | https://jobs.ashbyhq.com/rothys | ashby | active | |
| Angi | https://jobs.ashbyhq.com/angi | ashby | active | |
| Poshmark (Ashby) | https://jobs.ashbyhq.com/poshmark | ashby | active | |
| Strava | https://jobs.ashbyhq.com/strava | ashby | active | |
| Tonal | https://jobs.ashbyhq.com/tonal | ashby | active | |
| Patreon | https://jobs.ashbyhq.com/patreon | ashby | active | |
| Circle.so (community platform) | https://jobs.ashbyhq.com/circle | ashby | active | |
| Ladder (fitness) | https://jobs.ashbyhq.com/ladder | ashby | active | |
| Ankorstore | https://jobs.ashbyhq.com/ankorstore | ashby | active | |
| Oyster | https://jobs.ashbyhq.com/oyster | ashby | active | |
| Zip (procurement) | https://jobs.ashbyhq.com/zip | ashby | active | |
| LifeStance Health | https://jobs.lever.co/lifestance | lever | active | |
| Sila Services (home services) | https://jobs.lever.co/sila | lever | active | |
| Ro | https://jobs.lever.co/ro | lever | active | |
| Farfetch | https://jobs.lever.co/farfetch | lever | active | |
| Zopa | https://jobs.lever.co/zopa | lever | active | |
| Rover | https://jobs.lever.co/rover | lever | active | |
| Nium | https://jobs.lever.co/nium | lever | active | |
| Anchorage Digital | https://jobs.lever.co/anchorage | lever | active | |
| Crypto.com | https://jobs.lever.co/crypto | lever | active | |
| Arcadia | https://jobs.lever.co/arcadia | lever | active | |
| Voltus | https://jobs.lever.co/voltus | lever | active | |
| Everlywell | https://jobs.lever.co/everlywell | lever | active | |
| Mindful (ADHD care) | https://jobs.lever.co/mindful | lever | active | |
| Enveda | https://jobs.lever.co/enveda | lever | active | |
| Immutable | https://jobs.lever.co/immutable | lever | active | |
| Kavak | https://jobs.lever.co/kavak | lever | active | |
| Zus Health | https://jobs.lever.co/zushealth | lever | active | |
| Relay (web3 messenger) | https://jobs.lever.co/relay | lever | active | |
| Viome | https://jobs.lever.co/viome | lever | active | |
| Synthego | https://jobs.lever.co/synthego | lever | active | |
| Arsenal Bio | https://jobs.lever.co/arsenalbio | lever | active | |

### US mid-market/SMB on Workable, BambooHR, JazzHR & Gem (105/105 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Greenlife Healthcare Staffing | https://apply.workable.com/greenlife-healthcare-staffing-1 | workable | active | |
| Staffing for Doctors | https://apply.workable.com/staffing-for-doctors | workable | active | |
| Quick Hire Staffing | https://apply.workable.com/quickhirestaffing | workable | active | |
| Triage Staffing | https://apply.workable.com/triagestaffing | workable | active | |
| Natilus | https://apply.workable.com/natilus | workable | active | |
| Destinus | https://apply.workable.com/destinusgroup | workable | active | |
| Intercontinental Engineering-Manufacturing | https://apply.workable.com/intercon-eng-mfg | workable | active | |
| New Flyer | https://apply.workable.com/new-flyer | workable | active | |
| Exponent Energy | https://apply.workable.com/exponent-energy | workable | active | |
| Vanguard EMS | https://apply.workable.com/vanguard-ems-inc | workable | active | |
| Anthro | https://apply.workable.com/anthro | workable | active | |
| Granite State Manufacturing | https://apply.workable.com/granite-state-manufacturing | workable | active | |
| Nextern | https://apply.workable.com/nextern | workable | active | |
| PowerLines | https://apply.workable.com/powerlines | workable | active | |
| Vertex Sigma Software | https://apply.workable.com/vertex-sigma-software | workable | active | |
| Sigma Defense | https://apply.workable.com/sigmadefense | workable | active | |
| Branching Minds | https://apply.workable.com/branchingminds | workable | active | |
| NOW Courier | https://apply.workable.com/now-courier | workable | active | |
| Pj Fitzpatrick | https://apply.workable.com/pj-fitz | workable | active | |
| SwiftX | https://apply.workable.com/swiftx-express | workable | active | |
| Al Warren Oil Company | https://apply.workable.com/al-warren-oil-company-inc | workable | active | |
| United Concrete | https://apply.workable.com/united-concrete | workable | active | |
| talentpluto | https://apply.workable.com/talentpluto | workable | active | |
| TherapyNotes | https://apply.workable.com/therapynotes | workable | active | |
| Common App | https://apply.workable.com/commonapp | workable | active | |
| Resource Innovations | https://apply.workable.com/resource-innovations | workable | active | |
| Rodizio Grill | https://apply.workable.com/rodizio-grill-1 | workable | active | |
| Riot Hospitality Group | https://apply.workable.com/riot-hospitality-group | workable | active | |
| The Common Market | https://apply.workable.com/the-common-market | workable | active | |
| REEF | https://apply.workable.com/nbrhd | workable | active | |
| I.Rice & Company | https://apply.workable.com/irice-and-company | workable | active | |
| Hilo by Aktiia | https://apply.workable.com/hilobyaktiia | workable | active | |
| Quantis | https://apply.workable.com/quantis | workable | active | |
| Optimile | https://apply.workable.com/optimile | workable | active | |
| D2B | https://apply.workable.com/d2b-1 | workable | active | |
| Foodics | https://apply.workable.com/foodics | workable | active | |
| RISE Enterprise | https://apply.workable.com/rise-enterprise | workable | active | |
| EatClub | https://apply.workable.com/eatclub | workable | active | |
| Charger Logistics | https://apply.workable.com/charger-logistics-inc | workable | active | |
| BrightOrder | https://apply.workable.com/brightorder | workable | active | |
| JOEY Restaurants | https://apply.workable.com/joey-restaurants-1 | workable | active | |
| UniUni Logistics (count unverified) | https://apply.workable.com/uniuni-logistics | workable | active | |
| Alfil Logistics (count unverified) | https://apply.workable.com/alfil-logistics | workable | active | |
| Marini HR | https://marinihr.bamboohr.com/careers | bamboohr | active | |
| Whitman | https://whitman.bamboohr.com/careers | bamboohr | active | |
| Emerald Charter Schools | https://emeraldcharterschools.bamboohr.com/careers | bamboohr | active | |
| Oregon Family School | https://oregonfamilyschool.bamboohr.com/careers | bamboohr | active | |
| Rainier Scholars | https://rainierscholars.bamboohr.com/careers | bamboohr | active | |
| Greene County Public Health | https://gcph.bamboohr.com/careers | bamboohr | active | |
| Trinity Tool | https://trinitytool.bamboohr.com/careers | bamboohr | active | |
| Bare (BambooHR board, unconfirmed) | https://bare.bamboohr.com/careers | bamboohr | active | |
| Technique Inc | https://techniqueinc.applytojob.com/apply/jobs | jazzhr | active | |
| NRO (National Reconnaissance Office) | https://nro.applytojob.com/apply/jobs | jazzhr | active | |
| Aerotech | https://aerotech.applytojob.com/apply/jobs | jazzhr | active | |
| GliaCell Technologies | https://gliacelltechnologies.applytojob.com/apply/jobs | jazzhr | active | |
| Porter Logistics | https://porterlogistics.applytojob.com/apply/jobs | jazzhr | active | |
| WME Express | https://wmeexpress.applytojob.com/apply/jobs | jazzhr | active | |
| Impact Workforce Solutions | https://iwsllc.applytojob.com/apply/jobs | jazzhr | active | |
| Amsive | https://amsive.applytojob.com/apply/jobs | jazzhr | active | |
| Ladgov Corporation | https://httpsladgovcomjobopenings.applytojob.com/apply/jobs | jazzhr | active | |
| Foxconn Industrial Internet (FII) | https://foxconnassemblyllc.applytojob.com/apply/jobs | jazzhr | active | |
| WGNSTAR | https://wgnstar.applytojob.com/apply/jobs | jazzhr | active | |
| NSI Industries | https://nsiindustries.applytojob.com/apply/jobs | jazzhr | active | |
| Rittal | https://rittal.applytojob.com/apply/jobs | jazzhr | active | |
| Codekeeper | https://codekeeper.applytojob.com/apply/jobs | jazzhr | active | |
| TicketManager | https://ticketmanager.applytojob.com/apply/jobs | jazzhr | active | |
| Labelmaster | https://labelmaster.applytojob.com/apply/jobs | jazzhr | active | |
| Computronix | https://cxusa.applytojob.com/apply/jobs | jazzhr | active | |
| Sphere (company unconfirmed) | https://sphere.applytojob.com/apply/jobs | jazzhr | active | |
| Exceptional Healthcare | https://exceptionalhealthcareinc.applytojob.com/apply/jobs | jazzhr | active | |
| Fair Haven Community Health Care | https://fairhavencommunityhealthcare.applytojob.com/apply/jobs | jazzhr | active | |
| Cassia Health | https://cassia.applytojob.com/apply/jobs | jazzhr | active | |
| Miami County Public Health | https://miamicounty.applytojob.com/apply/jobs | jazzhr | active | |
| PainPoint Health | https://painpointhealth.applytojob.com/apply/jobs | jazzhr | active | |
| Whittier Health Network | https://whittierhealthnetwork.applytojob.com/apply/jobs | jazzhr | active | |
| Fonemed | https://fonemed.applytojob.com/apply/jobs | jazzhr | active | |
| ODMHSAS (Oklahoma mental health dept.) | https://odmhsas.applytojob.com/apply/jobs | jazzhr | active | |
| Aspen Medical | https://aspenmedical.applytojob.com/apply/jobs | jazzhr | active | |
| CECP | https://cecp.applytojob.com/apply/jobs | jazzhr | active | |
| JazzHR 'landing' board (unconfirmed) | https://landing.applytojob.com/apply/jobs | jazzhr | active | |
| Linktree | https://jobs.gem.com/linktree | gem | active | |
| Fetch | https://jobs.gem.com/fetch | gem | active | |
| Bilt | https://jobs.gem.com/bilt | gem | active | |
| Motion (AI calendar) | https://jobs.gem.com/motion | gem | active | |
| Paces | https://jobs.gem.com/paces | gem | active | |
| Nominal | https://jobs.gem.com/nominal | gem | active | |
| Fabric (health) | https://jobs.gem.com/fabrichealth | gem | active | |
| Cartwheel | https://jobs.gem.com/cartwheel-1 | gem | active | |
| Félix | https://jobs.gem.com/felix | gem | active | |
| Superblocks | https://jobs.gem.com/superblocks | gem | active | |
| Vantora | https://jobs.gem.com/up-labs | gem | active | |
| StartupTAP | https://jobs.gem.com/startuptap | gem | active | |
| Function Health | https://jobs.gem.com/function-health | gem | active | |
| Bolna | https://jobs.gem.com/bolna | gem | active | |
| Agora | https://jobs.gem.com/agora | gem | active | |
| Apartment List | https://jobs.gem.com/apartment-list | gem | active | |
| Productboard | https://jobs.gem.com/productboard | gem | active | |
| Logixboard | https://jobs.gem.com/logixboard | gem | active | |
| Inception | https://jobs.gem.com/inception | gem | active | |
| Jetty | https://jobs.gem.com/jetty-careers | gem | active | |
| Roe AI | https://jobs.gem.com/roe-ai | gem | active | |
| Gem (recruiting software) | https://jobs.gem.com/gem | gem | active | |
| Emerge Career (possibly stale) | https://jobs.gem.com/emerge-career | gem | active | |
| Black Ore (possibly stale) | https://jobs.gem.com/black-ore | gem | active | |
| Myriad Technology (likely stale) | https://jobs.gem.com/myriad-technology | gem | active | |

### Consumer lifestyle: fitness, wellness, beauty, pets, home, games, events (88/88 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| BetterHelp | https://job-boards.greenhouse.io/betterhelp | greenhouse | active | |
| Fashion Nova | https://job-boards.greenhouse.io/fashionnova | greenhouse | active | |
| Ōura | https://job-boards.greenhouse.io/oura | greenhouse | active | |
| Wellhub (Gympass) | https://job-boards.greenhouse.io/gympass | greenhouse | active | |
| WW (Weight Watchers) | https://job-boards.greenhouse.io/ww | greenhouse | active | |
| KRAFTON | https://job-boards.greenhouse.io/krafton | greenhouse | active | |
| Genius Sports | https://job-boards.greenhouse.io/geniussports | greenhouse | active | |
| SimpliSafe | https://job-boards.greenhouse.io/simplisafe | greenhouse | active | |
| Later | https://job-boards.greenhouse.io/later | greenhouse | active | |
| The Farmer's Dog | https://job-boards.greenhouse.io/thefarmersdog | greenhouse | active | |
| PrizePicks | https://job-boards.greenhouse.io/prizepicks | greenhouse | active | |
| StubHub | https://job-boards.greenhouse.io/stubhubinc | greenhouse | active | |
| Hudl | https://job-boards.greenhouse.io/hudl | greenhouse | active | |
| AXS | https://job-boards.greenhouse.io/axs | greenhouse | active | |
| Gymshark | https://job-boards.greenhouse.io/gymshark | greenhouse | active | |
| Nextdoor | https://job-boards.greenhouse.io/nextdoor | greenhouse | active | |
| onX | https://job-boards.greenhouse.io/onxmaps | greenhouse | active | |
| Harry's | https://job-boards.greenhouse.io/harrys | greenhouse | active | |
| SHEIN | https://job-boards.greenhouse.io/shein | greenhouse | active | |
| MyFitnessPal | https://job-boards.greenhouse.io/myfitnesspal | greenhouse | active | |
| Bombas | https://job-boards.greenhouse.io/bombas | greenhouse | active | |
| Underdog | https://job-boards.greenhouse.io/underdog | greenhouse | active | |
| AG1 | https://job-boards.greenhouse.io/ag1 | greenhouse | active | |
| Insomniac Games | https://job-boards.greenhouse.io/insomniac | greenhouse | active | |
| Dollar Shave Club | https://job-boards.greenhouse.io/dollarshaveclub | greenhouse | active | |
| Peak Design | https://job-boards.greenhouse.io/peakdesign | greenhouse | active | |
| Bandai Namco Entertainment | https://job-boards.greenhouse.io/bandainamco | greenhouse | active | |
| Headspace | https://job-boards.greenhouse.io/hs | greenhouse | active | |
| Gearbox | https://job-boards.greenhouse.io/gearbox | greenhouse | active | |
| BARK | https://job-boards.greenhouse.io/bark | greenhouse | active | |
| Future (fitness, ownership unconfirmed) | https://job-boards.greenhouse.io/future | greenhouse | active | |
| Fetch (pet insurance, ownership unconfirmed) | https://job-boards.greenhouse.io/fetch | greenhouse | active | |
| e.l.f. Beauty | https://jobs.lever.co/elfbeauty | lever | active | |
| Match Group | https://jobs.lever.co/matchgroup | lever | active | |
| Kabam | https://jobs.lever.co/kabam | lever | active | |
| Dream Games | https://jobs.lever.co/dreamgames | lever | active | |
| Wattpad | https://jobs.lever.co/wattpad | lever | active | |
| Raya (unconfirmed) | https://jobs.lever.co/raya | lever | active | |
| TeamSnap | https://jobs.lever.co/teamsnap | lever | active | |
| Yardzen | https://jobs.lever.co/yardzen | lever | active | |
| AllTrails | https://jobs.lever.co/alltrails | lever | active | |
| Hims & Hers | https://jobs.ashbyhq.com/hims-and-hers | ashby | active | |
| Voodoo | https://jobs.ashbyhq.com/voodoo | ashby | active | |
| BeReal | https://jobs.ashbyhq.com/bereal | ashby | active | |
| Sleeper | https://jobs.ashbyhq.com/sleeper | ashby | active | |
| Brooklinen | https://jobs.ashbyhq.com/brooklinen | ashby | active | |
| Posh | https://jobs.ashbyhq.com/posh | ashby | active | |
| Partiful | https://jobs.ashbyhq.com/partiful | ashby | active | |
| Quora | https://jobs.ashbyhq.com/quora | ashby | active | |
| Sorare | https://jobs.ashbyhq.com/sorare | ashby | active | |
| Yubo | https://jobs.ashbyhq.com/yubo | ashby | active | |
| Second Dinner | https://jobs.ashbyhq.com/seconddinner | ashby | active | |
| thatgamecompany | https://jobs.ashbyhq.com/thatgamecompany | ashby | active | |
| Celsius (Workable, ownership unconfirmed) | https://apply.workable.com/celsius | workable | active | |
| Petco | https://petco.wd1.myworkdayjobs.com/External | workday | active | |
| Life Time | https://lifetime.wd1.myworkdayjobs.com/lifetime | workday | active | |
| Topgolf Callaway Brands | https://tcbrands.wd1.myworkdayjobs.com/callaway-careers | workday | active | |
| YETI | https://yeticoolers.wd5.myworkdayjobs.com/YETI | workday | active | |
| Bumble | https://bumble.wd3.myworkdayjobs.com/Bumble_Careers | workday | active | |
| Columbia Sportswear | https://columbiasportswearcompany.wd5.myworkdayjobs.com/CSC_Careers | workday | active | |
| LEGO | https://lego.wd103.myworkdayjobs.com/LEGO_External | workday | active | |
| Spin Master | https://spinmaster.wd3.myworkdayjobs.com/SpinMaster_Careers | workday | active | |
| Dick's Sporting Goods | https://dickssportinggoods.wd1.myworkdayjobs.com/DSG | workday | active | |
| Skechers | https://skechers.wd5.myworkdayjobs.com/One-career-site | workday | active | |
| Deckers | https://deckers.wd5.myworkdayjobs.com/Deckers | workday | active | |
| Kontoor Brands | https://kbi.wd5.myworkdayjobs.com/Kontoor | workday | active | |
| Nu Skin | https://nuskin.wd5.myworkdayjobs.com/nuskin | workday | active | |
| Beachbody (BODi) | https://beachbody.wd1.myworkdayjobs.com/Careers | workday | active | |
| DraftKings | https://draftkings.wd1.myworkdayjobs.com/DraftKings | workday | active | |
| Cinemark | https://cinemark.wd1.myworkdayjobs.com/cinemark | workday | active | |
| Scotts Miracle-Gro | https://scottsmiraclegro.wd5.myworkdayjobs.com/SMGExternal | workday | active | |
| Orangetheory Fitness | https://orangetheory.wd1.myworkdayjobs.com/orangetheory | workday | active | |
| Purpose Brands | https://purposebrands.wd503.myworkdayjobs.com/purposebrands | workday | active | |
| POWDR | https://powdr.wd12.myworkdayjobs.com/POWDR_Careers | workday | active | |
| Pet Supermarket (Pet Retail Brands) | https://petretailbrands.wd5.myworkdayjobs.com/External_Career_Site_Pet_Supermarket_Inc | workday | active | |
| VCA / Banfield vet hospitals | https://vca.wd1.myworkdayjobs.com/BFCareers | workday | active | |
| Taymax (Planet Fitness franchisee) | https://taymax.wd5.myworkdayjobs.com/External_Careers | workday | active | |
| Collectors | https://collectorsuniverse.wd1.myworkdayjobs.com/collectors | workday | active | |
| Ticketmaster (Live Nation) | https://livenation.wd1.myworkdayjobs.com/TMExternalSite | workday | active | |
| Light & Wonder | https://lnw.wd5.myworkdayjobs.com/LightWonderExternalCareers | workday | active | |
| Excel Fitness | https://excelfitness.wd5.myworkdayjobs.com/Excel_Fitness | workday | active | |
| Alterra Mountain (Deer Valley) | https://alterra.wd1.myworkdayjobs.com/DeerValleyResort | workday | active | |
| Life Fitness | https://lifefitness.wd1.myworkdayjobs.com/searchLFN | workday | active | |
| Tractor Supply (low confidence) | https://tsc.wd12.myworkdayjobs.com/TSC-Careers | workday | active | |
| Ilitch (Little Caesars) | https://ilitch.wd5.myworkdayjobs.com/LC | workday | active | |
| Wawa | https://wawa.wd1.myworkdayjobs.com/careers | workday | active | |
| Fogo de Chão | https://fogo.wd5.myworkdayjobs.com/Fogo | workday | active | |
| WeWork | https://wework.wd1.myworkdayjobs.com/WeWork | workday | active | |

### Fortune 500 & large enterprises (Workday + Oracle Fusion) (115/115 in prod)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Academy Sports + Outdoors | https://academy.wd1.myworkdayjobs.com/Careers | workday | active | |
| Ally Financial | https://ally.wd1.myworkdayjobs.com/Ally | workday | active | |
| Baker Hughes | https://bakerhughes.wd5.myworkdayjobs.com/BakerHughes | workday | active | |
| Barclays | https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays | workday | active | |
| BMO | https://bmo.wd3.myworkdayjobs.com/External | workday | active | |
| Burlington | https://burlington.wd5.myworkdayjobs.com/BurlingtonCareers | workday | active | |
| Campbell's | https://campbellsoup.wd5.myworkdayjobs.com/ExternalCareers_GlobalSite | workday | active | |
| CarMax | https://carmax.wd1.myworkdayjobs.com/External | workday | active | |
| Carrier | https://carrier.wd5.myworkdayjobs.com/jobs | workday | active | |
| Church & Dwight | https://churchdwight.wd1.myworkdayjobs.com/chdcareers | workday | active | |
| CME Group | https://cmegroup.wd1.myworkdayjobs.com/cme_careers | workday | active | |
| CNA | https://cna.wd1.myworkdayjobs.com/CNA_Careers | workday | active | |
| ConocoPhillips (eQuest) | https://conocophillips.wd1.myworkdayjobs.com/eQuest | workday | active | |
| Danaher | https://danaher.wd1.myworkdayjobs.com/DanaherJobs | workday | active | |
| Dollar Tree | https://dollartree.wd5.myworkdayjobs.com/dollartreeus | workday | active | |
| Dover | https://dover.wd103.myworkdayjobs.com/Dover | workday | active | |
| Dow | https://dow.wd1.myworkdayjobs.com/ExternalCareers | workday | active | |
| DuPont | https://dupont.wd5.myworkdayjobs.com/Jobs | workday | active | |
| eBay | https://ebay.wd5.myworkdayjobs.com/apply | workday | active | |
| Equifax | https://equifax.wd5.myworkdayjobs.com/External | workday | active | |
| Fidelity Investments | https://fmr.wd1.myworkdayjobs.com/FidelityCareers | workday | active | |
| Fiserv | https://fiserv.wd5.myworkdayjobs.com/EXT | workday | active | |
| Franklin Templeton (Clarion sites) | https://franklintempleton.wd5.myworkdayjobs.com/Jobs-Clarion | workday | active | |
| Global Payments (TSYS) | https://tsys.wd1.myworkdayjobs.com/TSYS | workday | active | |
| Henry Schein | https://henryschein.wd1.myworkdayjobs.com/External_Careers | workday | active | |
| Hewlett Packard Enterprise | https://hpe.wd5.myworkdayjobs.com/Jobsathpe | workday | active | |
| Huntington Bancshares | https://huntington.wd12.myworkdayjobs.com/HNBcareers | workday | active | |
| Intel | https://intel.wd1.myworkdayjobs.com/External | workday | active | |
| Invesco | https://invesco.wd1.myworkdayjobs.com/IVZ | workday | active | |
| J.M. Smucker | https://smucker.wd5.myworkdayjobs.com/US_External_Careers | workday | active | |
| Johnson Controls | https://jci.wd5.myworkdayjobs.com/JCI | workday | active | |
| KeyBank | https://keybank.wd5.myworkdayjobs.com/External_Career_Site | workday | active | |
| KLA | https://kla.wd1.myworkdayjobs.com/Search | workday | active | |
| Lamb Weston | https://lambweston.wd1.myworkdayjobs.com/Lamb_External | workday | active | |
| LPL Financial | https://lplfinancial.wd1.myworkdayjobs.com/External | workday | active | |
| M&T Bank | https://mtb.wd5.myworkdayjobs.com/MTB | workday | active | |
| Marathon Petroleum | https://mpc.wd1.myworkdayjobs.com/MPCCareers | workday | active | |
| Mars | https://mars.wd3.myworkdayjobs.com/External | workday | active | |
| Motorola Solutions | https://motorolasolutions.wd5.myworkdayjobs.com/Careers | workday | active | |
| Nasdaq | https://nasdaq.wd1.myworkdayjobs.com/Global_External_Site | workday | active | |
| Northern Trust | https://ntrs.wd1.myworkdayjobs.com/northerntrust | workday | active | |
| Northwestern Mutual | https://northwesternmutual.wd5.myworkdayjobs.com/CORPORATE-CAREERS | workday | active | |
| Occidental | https://oxy.wd5.myworkdayjobs.com/Corporate | workday | active | |
| Otis | https://otis.wd504.myworkdayjobs.com/REC_Ext_Gateway | workday | active | |
| Owens & Minor | https://owensminor.wd1.myworkdayjobs.com/OMCareers | workday | active | |
| Raymond James | https://raymondjames.wd1.myworkdayjobs.com/RaymondJamesCareers | workday | active | |
| RBC | https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1 | workday | active | |
| S&P Global | https://spgi.wd5.myworkdayjobs.com/SPGI_Careers | workday | active | |
| Sanford Health | https://sanford.wd5.myworkdayjobs.com/SanfordHealth | workday | active | |
| Santander US | https://santander.wd3.myworkdayjobs.com/SantanderCareers | workday | active | |
| Stanley Black & Decker | https://sbdinc.wd1.myworkdayjobs.com/Stanley_Black_Decker_Career_Site | workday | active | |
| State Street | https://statestreet.wd1.myworkdayjobs.com/eQuest | workday | active | |
| Stellantis (India site) | https://stellantis.wd3.myworkdayjobs.com/External_Career_Site_ID01 | workday | active | |
| Sutter Health | https://sutterhealth.wd1.myworkdayjobs.com/SH | workday | active | |
| T. Rowe Price | https://troweprice.wd5.myworkdayjobs.com/TRowePrice | workday | active | |
| TD Bank | https://td.wd3.myworkdayjobs.com/TD_Bank_Careers | workday | active | |
| The Coca-Cola Company | https://coke.wd1.myworkdayjobs.com/coca-cola-careers | workday | active | |
| TJX | https://tjx.wd1.myworkdayjobs.com/TJX_EXTERNAL | workday | active | |
| Toyota Motor North America | https://toyota.wd503.myworkdayjobs.com/TMNA | workday | active | |
| Trane Technologies | https://tranetechnologies.wd12.myworkdayjobs.com/Trane_Technologies_Careers | workday | active | |
| U.S. Bank | https://usbank.wd1.myworkdayjobs.com/US_Bank_Careers | workday | active | |
| Unilever | https://unilever.wd3.myworkdayjobs.com/Unilever_Experienced_Professionals | workday | active | |
| Wells Fargo | https://wf.wd1.myworkdayjobs.com/WellsFargoJobs | workday | active | |
| Williams Companies | https://williams.wd5.myworkdayjobs.com/External | workday | active | |
| Xcel Energy | https://xcelenergy.wd1.myworkdayjobs.com/External | workday | active | |
| JPMorgan Chase | https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| BNY | https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| Macy's | https://ebwh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| Albertsons Companies | https://eofd.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| WM (Waste Management) | https://emcm.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/WMCareers/jobs | oracle_fusion | active | |
| Wesco | https://eklm.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| EECOL (Wesco) | https://eklm.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| Staples | https://fa-exhh-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/StaplesInc/jobs | oracle_fusion | active | |
| Northwell Health | https://eppr.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs | oracle_fusion | active | |
| Mount Sinai Health System | https://ejis.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| Texas Children's | https://eohh.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| UChicago Medicine | https://fa-etnf-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| Molina Healthcare | https://hckd.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| NOV | https://egay.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_4001/jobs | oracle_fusion | active | |
| Frontgrade Technologies | https://exzj.fa.us8.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| FirstEnergy | https://fa-etjd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/FirstEnergyCareers/jobs | oracle_fusion | active | |
| DTCC | https://ebxr.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Hearst | https://eevd.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| Perficient | https://fa-etqd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Crawford & Company | https://fa-esau-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/crawco-jobs/jobs | oracle_fusion | active | |
| Cantor Fitzgerald / BGC | https://hdow.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1003/jobs | oracle_fusion | active | |
| Westpac | https://ebuu.fa.ap1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| First Abu Dhabi Bank (FAB) | https://ehjd.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/fabCareers/jobs | oracle_fusion | active | |
| Bank of England | https://eoff.fa.em1.ukg.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| El Paso Electric | https://ibrvjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Suncorp | https://fa-evew-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Verisk | https://fa-ewmy-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Emergent Holdings | https://ejko.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs | oracle_fusion | active | |
| Definity Insurance | https://hdks.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Careers-Definity/jobs | oracle_fusion | active | |
| Ascot Group | https://fa-emkq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| UL Solutions | https://fa-eups-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/ULSolutionsCareers/jobs | oracle_fusion | active | |
| Honeywell Aerospace | https://icfcjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Aerospace/jobs | oracle_fusion | active | |
| Howmet Aerospace | https://fa-exty-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Coherent | https://hcwp.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2004/jobs | oracle_fusion | active | |
| Yum! Brands | https://eczd.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Corsair | https://edix.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| KIK Consumer Products | https://edwa.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/jobs | oracle_fusion | active | |
| Wood | https://ehif.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Oceaneering | https://ebfr.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobs/jobs | oracle_fusion | active | |
| Technip Energies | https://hcxg.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| AutoZone | https://egud.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| GM Financial | https://fa-exvu-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Daimler Truck | https://fa-exdu-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Subaru | https://hcal.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs | oracle_fusion | active | |
| TTX | https://ejjc.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| Caesars Entertainment | https://edmn.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Marriott International | https://ejwl.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs | oracle_fusion | active | |
| Hilton | https://efet.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |
| Hilton Grand Vacations | https://efuq.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/HiltonGrandVacations/jobs | oracle_fusion | active | |
| IHG Hotels & Resorts | https://fa-evax-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs | oracle_fusion | active | |

## Wave 5

Ran later on 2026-09-19 after checking in with the user: waves 1-4 had already covered nearly every standard sector, so this wave targeted sectors that were thin or missing — agriculture/agtech, legal services (deeper than wave 3's staffing/legal/consulting pass), and international (APAC, LATAM) — via four parallel research forks. Prod totals before wave 5: 1331 active / 31 pending / 22 rejected.

### Agriculture & agtech (11/13 researched, 3 flagged pending)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Land O'Lakes | https://landolakes.wd1.myworkdayjobs.com/LandOLakes | workday | active | |
| AeroFarms | https://aerofarms.applytojob.com/apply | jazzhr | active | |
| Elanco | https://elanco.wd5.myworkdayjobs.com/External_Career | workday | active | |
| FMC Corporation | https://fmc.wd12.myworkdayjobs.com/FMC | workday | active | shape-only (JS SPA) |
| Inari Agriculture | https://job-boards.greenhouse.io/inariagriculture | greenhouse | active | |
| Farmers Business Network | https://apply.workable.com/farmers-business-network/ | workable | active | fork gave a job-detail URL on the company's own subdomain, 422'd; corrected to the `apply.workable.com/{account}/` shape |
| Valmont Industries | https://valmont.wd1.myworkdayjobs.com/ValmontCareers | workday | active | |
| Wilbur-Ellis | https://wilburellis.wd12.myworkdayjobs.com/WILBUR-ELLIS | workday | active | shape-only (JS SPA) |
| Sound Agriculture | https://job-boards.greenhouse.io/soundagriculture | greenhouse | active | |
| Bayer | https://talent.bayer.com/careers/job/562949978570371-... | eightfold | active (via /jobs) | Eightfold has no static shape, 422'd on admin endpoint; real job URL pulled from its sitemap (`talent.bayer.com`, tenant host differs from `bayer.eightfold.ai`) and submitted via `/jobs` instead |
| Darling Ingredients | https://darlingii.applicantpro.com/jobs/4172619.html | ApplicantPro (isolved) | pending (submitted) | |
| J.R. Simplot Company | https://careers.simplot.com/job/Boise-Associate-Agronomist-II-ID-83706-1211/1290756100/ | unidentified custom portal | pending (submitted) | |
| Nutrien | https://jobs.nutrien.com/North-America/job/Operator/32466-en_US/ | unidentified custom portal | pending (submitted) | possibly same vendor as Simplot |

### Legal services (16/16 added, 1 flagged pending, 1 skipped)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Latham & Watkins | https://careers-lw.icims.com | icims | active | |
| Sidley Austin | https://careers-sidley.icims.com | icims | active | |
| Integreon | https://careers-integreon.icims.com | icims | active | legal process outsourcing |
| Wilson Sonsini (WSGR) | https://wsgr.wd503.myworkdayjobs.com/WSGR | workday | active | shape-only |
| Goodwin Procter | https://goodwinprocter.wd5.myworkdayjobs.com/External_Careers | workday | active | shape-only |
| Clio | https://clio.wd3.myworkdayjobs.com/ClioCareerSite | workday | active | legal practice-mgmt software |
| Epiq Systems | https://epiqsystems.wd5.myworkdayjobs.com/Epiq_Careers | workday | active | eDiscovery/legal services |
| Perkins Coie | https://perkinscoie.wd1.myworkdayjobs.com/perkinscoieexternal | workday | active | |
| Relativity (eDiscovery) | https://kcura.wd1.myworkdayjobs.com/External_Career_Site | workday | active | named to disambiguate from the existing "Relativity Space" row (different company) |
| DLA Piper | https://dlapiper.wd1.myworkdayjobs.com/dlapiper | workday | active | |
| White & Case | https://whitecase.wd1.myworkdayjobs.com/External | workday | active | also runs a Taleo tenant (see skipped below) |
| Filevine | https://jobs.lever.co/filevine | lever | active | legal case-mgmt software |
| Litify | https://job-boards.greenhouse.io/litify | greenhouse | active | Salesforce-based legal practice-mgmt |
| Ironclad | https://jobs.ashbyhq.com/ironcladhq | ashby | active | contract lifecycle mgmt |
| Spellbook | https://jobs.ashbyhq.com/spellbook.legal | ashby | active | AI contract drafting |
| EvenUp | https://jobs.ashbyhq.com/evenup | ashby | active | legal AI for personal-injury claims |
| Axiom Law | https://www.axiomlaw.com/careers/lawyers/available-positions/8687361002 | unclear (custom domain, job-ID pattern resembles embedded Greenhouse) | pending (submitted) | testing embedded-match detection |

Skipped: **Jones Day** (viRecruit/viGlobal, 77 live postings confirmed, but no deep-linkable individual job URL exists — filter-driven UI, no guessed URL submitted per the never-template rule). **White & Case's Taleo tenant** (`whitecase.taleo.net`) — real live requisition found, but not submitted since the company is already covered via its Workday row above (duplicate-coverage skip, same convention as wave 4).

### International — APAC (11/17 researched, 3 flagged pending)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Razorpay | https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited | greenhouse | active | India fintech |
| Ninja Van | https://jobs.lever.co/ninjavan | lever | active | Singapore logistics |
| Aspire | https://job-boards.greenhouse.io/aspire | greenhouse | active | Singapore fintech |
| CRED | https://jobs.lever.co/cred | lever | active | India fintech |
| ShopBack | https://jobs.lever.co/shopback-2 | lever | active | Singapore fintech/e-commerce |
| PhonePe | https://job-boards.greenhouse.io/phonepe | greenhouse | active | India fintech |
| Rakuten | https://rakuten.wd1.myworkdayjobs.com/RakutenInc | workday | active | Japan; main tenant only, several other Rakuten Workday tenants exist unadded |
| GoTo Group | https://jobs.lever.co/GoToGroup | lever | active | Indonesia (Gojek+Tokopedia) |
| Coupang | https://boards.greenhouse.io/coupang | greenhouse | active | South Korea e-commerce; older `boards.greenhouse.io` host |
| Coins.ph | https://jobs.lever.co/coins | lever | active | Philippines crypto/fintech |
| Groww | https://job-boards.eu.greenhouse.io/groww | greenhouse | **422, not added** | genuine adapter gap: `_GREENHOUSE_URL_RE` only matches `job-boards.\|boards.greenhouse.io`, not the `eu.` region subdomain — real Greenhouse board, not company-specific |
| Canva | https://jobs.smartrecruiters.com/canva/6000000001379665-... | SmartRecruiters | pending (submitted) | already-known unsupported platform per earlier waves |
| LINE Corporation | https://careers.linecorp.com/jobs/2913/ | in-house/custom | pending (submitted) | |
| Toss / Viva Republica | https://toss.im/career/job-detail?job_id=4553599003 | in-house/custom | pending (submitted) | South Korea fintech |

### International — LATAM (9/9 added, 2 flagged pending)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| EBANX | https://job-boards.greenhouse.io/ebanx | greenhouse | active | Brazil payments |
| Clara | https://job-boards.greenhouse.io/clara | greenhouse | active | Mexico corporate cards |
| VTEX | https://job-boards.greenhouse.io/vtex | greenhouse | active | Brazil e-commerce platform |
| Grupo QuintoAndar | https://job-boards.greenhouse.io/quintoandar | greenhouse | active | Brazil/Portugal real estate |
| Stone (StoneCo) | https://job-boards.greenhouse.io/stone | greenhouse | active | Brazil payments |
| Banco Inter (Inter&Co) | https://job-boards.greenhouse.io/inter | greenhouse | active | Brazil digital bank |
| Addi | https://jobs.ashbyhq.com/addi | ashby | active | Colombia BNPL/fintech |
| Rappi | https://rappi.wd12.myworkdayjobs.com/Rappi_jobs | workday | active | Colombia super-app, shape-only |
| Hotmart | https://job-boards.eu.greenhouse.io/hotmartcareersbr | greenhouse | **422, not added** | same `eu.greenhouse.io` adapter gap as Groww above |
| Assaí Atacadista | https://assai.gupy.io/jobs/11935518 | Gupy | pending (submitted) | major Brazilian ATS, not yet supported |
| Lojas Renner | https://lojasrenner.gupy.io/jobs/8997587 | Gupy | pending (submitted) | separate per-tenant domain from Assaí; will land as its own pending row |

**Known gap found this wave:** `app/services/adapters/greenhouse.py`'s `_GREENHOUSE_URL_RE` only matches `job-boards.greenhouse.io` and `boards.greenhouse.io`, not regional subdomains like `job-boards.eu.greenhouse.io` — a real, working Greenhouse board (Hotmart, Groww confirmed) 422s on the admin endpoint purely because of the host regex. Worth a one-line regex fix in a future `implement-crawl-adapter` pass; not changed here (out of scope for a discovery run).

**Totals after wave 5:** 1376 active, 32 pending, 22 rejected (immediately after submission — most of the 9 new `/jobs` pending submissions were still `scan_status: pending` on their own URL row, not yet resolved into a `crawl_source` row, due to the same worker-saturation issue noted in Known Problems below; expect the pending count to climb further as the backlog drains).

## Wave 6

Ran right after wave 5, still 2026-09-19: user asked to continue, so this wave targeted international regions not yet touched — Middle East/Israel, Africa, Eastern Europe, Canada — via four more parallel forks. All four hit the **session-wide WebSearch cap (200/200)** partway through, so each came back well short of the ~15-18 target; the user was told before writing anything, and then said to focus future waves on USA jobs but to still write the already-verified international finds since the research was already done. Prod totals before wave 6: 1378 active / 36 pending / 22 rejected.

| Company | Board URL | ATS | Status | Region | Note |
|---|---|---|---|---|---|
| Torq | https://job-boards.greenhouse.io/torq | greenhouse | active | Israel | AI SOC/security-automation |
| Kuda Technologies | https://apply.workable.com/kuda/ | workable | active | Nigeria | digital bank |
| M-KOPA | https://jobs.ashbyhq.com/M-KOPA | ashby | active | Kenya/Uganda/Nigeria | connected-asset financing |
| Wave Mobile Money | https://job-boards.greenhouse.io/wavemm1 | greenhouse | active | Senegal/Côte d'Ivoire/Mali/Uganda | |
| UiPath | https://jobs.ashbyhq.com/uipath | ashby | active | Romania-founded | RPA |
| Nord Security (NordVPN) | https://jobs.ashbyhq.com/nord-security | ashby | active | Lithuania-founded | |
| Lightspeed Commerce | https://job-boards.greenhouse.io/lightspeedhq | greenhouse | active | Canada (Montreal) | |
| Coveo | https://job-boards.greenhouse.io/coveoen | greenhouse | active | Canada (Quebec City) | |
| Vidyard | https://boards.greenhouse.io/vidyard | greenhouse | active | Canada (Kitchener) | |
| Benevity | https://boards.greenhouse.io/benevity | greenhouse | active | Canada (Calgary) | |
| League Inc. | https://job-boards.greenhouse.io/leagueinc | greenhouse | active | Canada (Toronto) | |
| Neo Financial | https://jobs.ashbyhq.com/neofinancial | ashby | active | Canada (Calgary) | |
| KOHO | https://jobs.ashbyhq.com/koho | ashby | active | Canada (Vancouver) | |
| Top Hat | https://jobs.ashbyhq.com/top-hat | ashby | active | Canada (Toronto) | |
| Jane App | https://jobs.lever.co/janeapp | lever | active | Canada (remote-first) | healthcare practice software |
| ApplyBoard | https://jobs.lever.co/applyboard | lever | active | Canada (Kitchener) | |
| CAE Inc | https://cae.wd3.myworkdayjobs.com/career | workday | active | Canada (Montreal) | shape-only |

No genuinely-unsupported-platform pending submissions this wave — none of the forks found a confirmed individual job-posting URL on a new platform before the search budget ran out.

**More `eu.greenhouse.io` adapter-gap hits (not added, same as wave 5's Hotmart/Groww):** Tamara (Saudi fintech, `job-boards.eu.greenhouse.io/tamara`, 31 live jobs) and Jumia (`job-boards.eu.greenhouse.io/jumia`, 21 live jobs) — both real, working Greenhouse boards that 422 on the admin endpoint purely due to the host regex. Third data point for the same one-line fix noted in wave 5.

**Totals after wave 6:** 1399 active, 36 pending, 22 rejected.

## Wave 7

Ran as a **separate, concurrent session** from waves 5-6, also on 2026-09-19 — this session was not aware of the other until it reached this point in the file. Targeted niche sectors not covered by waves 1-4 (all of which had already covered every broad vertical the playbook suggests): K-12 school districts & higher-ed, restaurants/QSR/hospitality, sports/entertainment/venues, and international (a different slice than wave 5's APAC/LATAM pass — this one skewed toward Japan/Korea/SE Asia/Brazil/Africa/UAE). Prod totals before wave 7: 1331 active / 31 pending / 22 rejected (fetched before the other session's wave 5-6 writes landed, so this wave's dedup exclusion list predates those — see overlap note below).

### Sports, entertainment & ticketing (9/11 added, 2 flagged pending)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Fanatics | https://job-boards.greenhouse.io/fanaticsinc | greenhouse | active | |
| Vivid Seats | https://job-boards.greenhouse.io/vividseatsllc | greenhouse | active | |
| Golden State Warriors | https://job-boards.greenhouse.io/goldenstatewarriors | greenhouse | active | |
| Lucky Strike Entertainment (Bowlero) | https://corporatecareers-luckystrikeentertainment.icims.com/ | icims | active | |
| Six Flags Entertainment | https://careers-sixflags.icims.com/ | icims | active | |
| PENN Entertainment | https://careersapply-pennentertainment.icims.com/ | icims | active | |
| TKO Group Holdings (WWE/UFC) | https://wwecorp.wd5.myworkdayjobs.com/TKO | workday | active | shape-only |
| PGA TOUR | https://pgatour.wd5.myworkdayjobs.com/PGATOURExternal | workday | active | shape-only |
| Dave & Buster's | https://daveandbusters.wd1.myworkdayjobs.com/en-US/Dave_and_Busters_Careers | workday | active | shape-only |
| Aristocrat Leisure | https://aristocrat.wd3.myworkdayjobs.com/en-US/AristocratExternalCareersSite | workday | active | shape-only; distinct from Light & Wonder (already in roster) |
| ESL FACEIT Group | https://apply.workable.com/efg/ | workable | active | esports league/tournament operator |
| Team Liquid | https://careers.teamliquid.com/jobs/1037950-global-early-careers | Teamtailor | pending (submitted) | |
| Manchester United | https://careers.manutd.com/postings/a081a592-ef5e-4b39-a5f7-70cda2599093 | Pinpoint HQ | pending (submitted) | |

### Restaurants, QSR & hospitality (10/11 added, 2 flagged pending, 1 failed)

| Company | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| Dutch Bros Coffee | https://dutchbros.wd1.myworkdayjobs.com/en-US/DBShops | workday | active | shape-only |
| Panera Bread | https://panerabread.wd5.myworkdayjobs.com/Panera_Careers | workday | active | shape-only |
| Cracker Barrel Old Country Store | https://cbrlgroup.wd5.myworkdayjobs.com/CrackerBarrelExternal | workday | active | shape-only |
| Whataburger | https://whataburger.wd5.myworkdayjobs.com/WAB_CAREERS | workday | active | shape-only |
| Restaurant Brands International (BK/Popeyes/Tim Hortons/Firehouse Subs) | https://rbi.wd3.myworkdayjobs.com/RBI_External_Career_Site | workday | active | shape-only, multi-brand corporate site |
| Sonesta International Hotels | https://reitmr.wd5.myworkdayjobs.com/Sonesta | workday | active | shape-only |
| Dine Brands Global (Applebee's/IHOP/Fuzzy's) | https://dinebrands.wd503.myworkdayjobs.com/RestaurantCareerSite | workday | active | shape-only |
| Omni Hotels & Resorts | https://externalhourly-omnihotels.icims.com/ | icims | active | |
| Highgate Hotels | https://externalmanagement-highgate.icims.com/ | icims | active | |
| Davidson Hospitality Group | https://management-davidsonhospitality.icims.com/ | icims | active | |
| BJ's Restaurants | https://careers.bjsrestaurants.com | — | **422, not added** | fork reported iCIMS-backed but gave a custom-domain front-end that doesn't match `detect_ats_source`'s iCIMS signature; needs the real `*.icims.com` subdomain, not found before this session's WebSearch budget ran out |
| Darden Restaurants (Olive Garden, LongHorn, Yard House, etc.) | https://darden.paradox.ai/co/DardenRestaurantSupportCenter/Job?job_id=PDX_DRSC_2B430CE4-501A-4ECD-85AA-95D6BA7845F9_88118 | Paradox.ai | pending (submitted) | |
| Jack in the Box | https://www.jackintheboxjobs.com/clients/19827/posting/9541681 | talentReef | pending (submitted) | Culver's and Potbelly also confirmed on talentReef, lower incremental value once one adapter exists |

Skipped: Chili's/Brinker (`brinker.taleo.net` DNS no longer resolves, migrated off Taleo to an unclear platform, dropped rather than guess).

### K-12 school districts & higher education (7/7 added, 2 flagged pending)

| Institution | Board URL | ATS | Status | Note |
|---|---|---|---|---|
| University of Chicago | https://uchicago.wd5.myworkdayjobs.com/External | workday | active | shape-only; distinct from "UChicago Medicine" already in roster |
| University of Texas at Austin | https://utaustin.wd1.myworkdayjobs.com/UTstaff | workday | active | shape-only |
| University of Maryland, College Park | https://umd.wd1.myworkdayjobs.com/UMCP | workday | active | shape-only |
| University of Wisconsin-Madison | https://wisconsin.wd1.myworkdayjobs.com/UW_Madison | workday | active | shape-only |
| American University | https://american.wd1.myworkdayjobs.com/AU | workday | active | shape-only |
| University of Arkansas System | https://uasys.wd5.myworkdayjobs.com/UASYS | workday | active | shape-only |
| University of Louisville | https://uofl.wd1.myworkdayjobs.com/UofLCareerSite | workday | active | shape-only |
| Atlanta Public Schools | https://www.applitrack.com/atlantapublicschools/onlineapp/default.aspx?AppliTrackJobID=5315 | Frontline AppliTrack | pending (submitted) | |
| San Jose Evergreen Community College District | https://sjeccd.peopleadmin.com/postings/4333 | PeopleAdmin | pending (submitted) | |

Notable: Los Angeles Unified School District (careers.lausd.org) runs SAP SuccessFactors (`career41.sapsf.com`, company code `losangel01`, already a supported adapter via embedded-match) but no individual job-requisition URL was found via search before the budget ran out — only category-filter links. Worth a follow-up submission once a real job URL is found.

### International — Asia, Latin America, Africa, Middle East (12/15 added, 2 flagged pending, 1 failed, 3 dupes)

| Company | Board URL | ATS | Status | Region | Note |
|---|---|---|---|---|---|
| InMobi | https://job-boards.greenhouse.io/inmobi | greenhouse | active | India | |
| Meesho | https://jobs.lever.co/meesho | lever | active | India | |
| PayPay | https://job-boards.greenhouse.io/paypay | greenhouse | active | Japan | |
| Seoul Robotics | https://job-boards.greenhouse.io/seoulrobotics | greenhouse | active | South Korea | |
| EasyGo | https://job-boards.greenhouse.io/easygo | greenhouse | active | Australia | |
| CI&T | https://jobs.lever.co/ciandt | lever | active | Brazil | |
| iFood | https://job-boards.greenhouse.io/ifoodcarreiras | greenhouse | active | Brazil | |
| Traveloka | https://traveloka.wd3.myworkdayjobs.com/Traveloka | workday | active | Indonesia | shape-only |
| ALX Africa | https://job-boards.greenhouse.io/alxafrica | greenhouse | active | South Africa/pan-Africa | edtech |
| Cobblestone Energy | https://job-boards.greenhouse.io/cobblestoneenergy | greenhouse | active | UAE | |
| Zomato | https://jobs.smartrecruiters.com/Zomato1/104244178-software-engineer-back-end | SmartRecruiters | pending (submitted) | India | already-known unsupported platform per wave 4/6 |
| MNT-Halan | https://jobs.halan.com/position/investor-relations-analyst/ | Zenats | pending (submitted) | Egypt | |
| Jumia | https://job-boards.eu.greenhouse.io/jumia | greenhouse | **422, not added** | Nigeria | same `eu.greenhouse.io` adapter-gap hit as wave 5/6's Hotmart/Groww/Tamara — fourth data point for the one-line regex fix |
| GoTo Group | https://jobs.lever.co/GoToGroup | lever | **409, dupe** | Indonesia | already added by the concurrent wave 5 session |
| Ninja Van | https://jobs.lever.co/ninjavan | lever | **409, dupe** | Singapore | already added by the concurrent wave 5 session |
| Coins.ph | https://jobs.lever.co/coins | lever | **409, dupe** | Philippines | already added by the concurrent wave 5 session |
| Rappi | https://rappi.wd12.myworkdayjobs.com/Rappi_jobs | workday | **409, dupe** | Colombia | already added by the concurrent wave 5 session |

**Concurrent-session note:** this wave's exclusion list was pulled before the other session's waves 5-6 writes landed, so 4 of its 17 international candidates turned out to already be active (409s, harmless) and one (Jumia) hit an adapter gap the other session had already independently found (Hotmart/Groww/Tamara). No corrective action needed — both are self-resolving (409 = no-op, and the `eu.greenhouse.io` regex gap is now a 4x-confirmed candidate for a follow-up fix). This session's own WebSearch budget also hit the 200/200 cap partway through the international fork, same as wave 6.

**Totals after wave 7:** 1434 active, 39 pending, 22 rejected.

## Submitted via `POST /jobs` (job-URL / domain submissions)

Unsupported platforms land as `pending` keyed by domain. Supported or embedded platforms resolve to `active`, sometimes on a different board than the submitted URL (e.g. Cleveland Clinic and Palo Alto Networks resolved to their Workday boards). Since commit `59aa263`, board registration runs in the scan worker, so a submission shows **no row yet** while its scan is still `pending`.

| Company | Submitted | Row in prod | Status / ATS |
|---|---|---|---|
| PayPal | https://paypal.eightfold.ai/careers/job/274904264573 | https://paypal.eightfold.ai | active / eightfold |
| American Express | https://aexp.eightfold.ai/careers/job/38842605 | (placeholder deleted) | – |
| USAA | https://www.usaajobs.com/job/san-antonio/senior-infrastructure-engineer-data-pro | https://usaa.wd1.myworkdayjobs.com/USAAJOBSWD | active / workday |
| Liberty Mutual | https://searchjobs.libertymutualgroup.com/careers/job/618519419340 | https://searchjobs.libertymutualgroup.com | active / eightfold |
| Fidelity | https://jobs.fidelity.com/en/jobs/2134523/director-data-platform-mainframe-devel | https://jobs.fidelity.com | rejected / None |
| Synopsys | https://synopsys.avature.net/careers/JobDetail/Validation-Verification-Eng-Sr-En | https://synopsys.avature.net | active / avature |
| Heitman | https://jobs.jobvite.com/heitman/job/oGgyAfwO | https://jobs.jobvite.com | rejected / None |
| McGraw Hill | https://careers.mheducation.com/jobs/6545 | https://careers.mheducation.com | active / icims |
| Ellucian | https://careers.ellucian.com/jobs/6309 | https://careers.ellucian.com | active / icims |
| CBRE | https://careers.cbre.com/en_US/careers/JobDetail/268297 | – | **no row yet** (returned 500 before fix `59aa263`; resubmitted, 202, scan queued) |
| Rocket Companies | https://careers.rocket.com/careers/r-081326/capital-markets-associate/ | https://careers.rocket.com | rejected / None |
| D.R. Horton | https://drhorton.taleo.net/careersection/2/jobdetail.ftl?job=2602082 | https://drhorton.taleo.net | pending / None |
| Texas Instruments | https://careers.ti.com/en/sites/CX/job/25009893 | https://careers.ti.com/en/sites/CX/job/25009893 | active / oracle_fusion |
| Lattice Semiconductor | https://careers-latticesemi.icims.com/jobs/3476/applications-eng-3/job | https://careers-latticesemi.icims.com | rejected / None |
| Keysight | https://careers-keysight.icims.com | https://careers-keysight.icims.com | active / icims |
| PowerSchool | https://careers3-powerschool.icims.com | https://careers3-powerschool.icims.com/ | active / clinch |
| Goldman Sachs | https://higher.gs.com | https://higher.gs.com | pending / None |
| Qualcomm | https://careers.qualcomm.com | https://careers.qualcomm.com | active / eightfold |
| AbbVie | https://careers.abbvie.com/en/job/senior-scientist-i-in-worcester-ma-jid-31706 | https://careers.abbvie.com | active / attrax |
| Eaton | https://eaton.eightfold.ai/careers/job/687238289031-lead-engineer-systems-engine | https://eaton.eightfold.ai | active / eightfold |
| TireHub (UKG) | https://recruiting.ultipro.com/HAW1005HAWNE/JobBoard/fb14a429-ca54-48db-b7e0-0c5 | https://recruiting.ultipro.com | rejected / None |
| UnitedHealth Group | https://careers.unitedhealthgroup.com/job/eden-prairie/senior-software-engineer- | https://careers.unitedhealthgroup.com/job/eden-prairie/senior-so | active / talentbrew |
| Larry H. Miller Senior Health (Paylocity) | https://recruiting.paylocity.com/recruiting/jobs/Details/4430542/Larry-H-Miller- | https://recruiting.paylocity.com | rejected / None |
| Boston Scientific | https://bostonscientific.eightfold.ai/careers/job/563602813281108-senior-ai-solu | https://bostonscientific.eightfold.ai | active / eightfold |
| Under Armour | https://careers.underarmour.com/job/Remote-Sr_-Product-Manager-Analytics-and-Dat | https://careers.underarmour.com/job/Remote-Sr_-Product-Manager-A | active / successfactors |
| The Hershey Company | https://careers.thehersheycompany.com/job/Hershey-Production-Operator-Reese-Plan | https://careers.thehersheycompany.com/job/Hershey-Production-Ope | active / successfactors |
| Mattel | https://jobs.smartrecruiters.com/mattelinc/744000146081909-mattel-retail-team-as | https://jobs.smartrecruiters.com | rejected / None |
| Genentech | https://careers.gene.com/us/en/job/ | https://careers.gene.com | active / phenom |
| HCA Healthcare | https://careers.hcahealthcare.com/jobs/16648854-sales-and-use-tax-intern | https://careers.hcahealthcare.com | rejected / None |
| Cleveland Clinic | https://jobs.clevelandclinic.org | https://ccf.wd1.myworkdayjobs.com/ClevelandClinicCareers | active / workday |
| Mayo Clinic | https://jobs.mayoclinic.org | https://jobs.mayoclinic.org/ | active / talentbrew |
| Colgate-Palmolive | https://jobs.colgate.com | https://jobs.colgate.com/ | active / successfactors |
| Electronic Arts | https://ea.gr8people.com | https://ea.gr8people.com | rejected / None |
| Estee Lauder | https://careers.elcompanies.com | https://careers.elcompanies.com | active / eightfold |
| Best Buy | https://careers.bestbuy.com/bby | https://careers.bestbuy.com | pending / None |
| Keurig Dr Pepper | https://kdrp.eightfold.ai | https://kdrp.eightfold.ai | pending / None |
| Lockheed Martin | https://lockheedmartin.eightfold.ai/careers | https://lockheedmartin.eightfold.ai | active / eightfold |
| Dollar General | https://login-dollargeneral.icims.com | https://login-dollargeneral.icims.com | pending / None |
| Maximus | https://maximus.avature.net/careers/FolderDetail/United-States-43607-TSCM-SME-Be | https://maximus.avature.net | pending / None |
| L3Harris | https://jobs.l3harris.com/job/Herndon-Software-Engineer-Intern-VA-20171/14133434 | https://jobs.l3harris.com/job/Herndon-Software-Engineer-Intern-V | active / successfactors |
| Huntington Ingalls (HII) | https://careers.huntingtoningalls.com/job/Pascagoula-JOINER-SKILLED-Miss/1410448 | https://careers.huntingtoningalls.com/job/Pascagoula-JOINER-SKIL | active / successfactors |
| SAIC | https://jobs.saic.com/jobs/17974700-senior-systems-engineer | https://jobs.saic.com | pending / None |
| Battelle | https://jobs.battelle.org | https://jobs.battelle.org | pending / None |
| Peraton | https://careers-peraton.icims.com | https://careers-peraton.icims.com/ | active / clinch |
| Jacobs | https://careers.jacobs.com | – | **no row yet** (returned 500 before fix `59aa263`; resubmitted, 202, scan queued) |
| AECOM | https://aecom.jobs | https://aecom.jobs/ | active / nlx |
| Sandia National Labs | https://sandia.jobs | https://sandia.jobs | pending / None |
| Harvard University | https://careers.harvard.edu | https://careers.harvard.edu | pending / None |
| Stanford University | https://careersearch.stanford.edu | https://careersearch.stanford.edu | pending / None |
| Johns Hopkins University | https://hiring.jhu.edu | https://hiring.jhu.edu | active / eightfold |
| Eightfold AI | https://app.eightfold.ai/careers/job/68763888174 | https://app.eightfold.ai | active / eightfold |
| ServiceNow | https://jobs.smartrecruiters.com/servicenow/744000149961559-staff-software-engin | https://jobs.smartrecruiters.com | rejected / None |
| IBM | https://careers.ibm.com/en_US/careers/JobDetail/Software-Developer-Intern-2027/1 | https://careers.ibm.com | pending / None |
| SAP | https://jobs.sap.com | https://jobs.sap.com/ | active / successfactors |
| Palo Alto Networks | https://jobs.paloaltonetworks.com/en/job/santa-clara/principal-engineer-software | https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcaree | active / workday |
| State Farm | https://careers-statefarm.icims.com | https://careers-statefarm.icims.com | pending / None |
| Progressive | https://careers.progressive.com/jobs/17648069-medical-claims-representative-trai | https://careers.progressive.com | pending / None |
| TriNet | https://trinet.eightfold.ai | https://trinet.eightfold.ai | active / eightfold |
| GlobalFoundries | https://globalfoundries.eightfold.ai | https://globalfoundries.eightfold.ai | active / eightfold |
| Deloitte (Belgium/Avature) | https://deloittebe.avature.net/en_US/careers/JobDetail/SAP-Supply-Chain-Project- | https://deloittebe.avature.net | pending / None |
| Kelly Services | https://jobs.smartrecruiters.com/PartneredStaffing-KellyServices/743999652752715 | https://jobs.smartrecruiters.com | rejected / None |
| Wolters Kluwer (SmartRecruiters) | https://jobs.smartrecruiters.com/WoltersKluwer1/83235277-application-support-spe | https://jobs.smartrecruiters.com | rejected / None |
| TEKsystems | https://careers-teksystems.icims.com | https://careers-teksystems.icims.com | pending / None |
| Insight Global | https://careers-insightglobal.icims.com/jobs | https://careers-insightglobal.icims.com | pending / None |
| Aerotek | https://careers-aerotek.icims.com | https://careers-aerotek.icims.com/ | active / clinch |
| EY | https://ey.taleo.net/careersection/gexp01/jobsearch.ftl | https://ey.taleo.net | pending / None |
| Grant Thornton (legacy Taleo) | https://gt.taleo.net/careersection/gt_careersite_external/jobdetail.ftl | https://gt.taleo.net | pending / None |
| Deere | https://jobs.deere.com/job/East-Moline-Engineer%2C-Software-Illi-61244/141889500 | https://jobs.deere.com/job/East-Moline-Engineer%2C-Software-Illi | active / successfactors |
| Royal Caribbean Group | https://jobs.royalcaribbeangroup.com/job/Miami-Analyst,-Revenue-Management-Opera | https://jobs.royalcaribbeangroup.com/job/Miami-Analyst,-Revenue- | active / successfactors |
| JetBlue | https://careers.jetblue.com/job/Boston-Airport-Operations-Crew-MA-02128/13287069 | https://careers.jetblue.com/job/Boston-Airport-Operations-Crew-M | active / successfactors |
| Charter / Spectrum | https://jobs.spectrum.com/job/englewood/associate-software-engineer/4673/9221009 | https://jobs.spectrum.com | pending / None |
| Toyota | https://careers.toyota.com/us/en/job/10324999/Software-Engineer | https://careers.toyota.com | pending / None |
| Hertz | https://www.hertz.com/gfj/courtesy-bus-driver-pasadena-ca-6a7d24cde3761521cdbca9 | https://www.hertz.com | pending / None |
| Hyatt | https://careers.hyatt.com/en-US/careers/details/10880/DAR000279 | https://careers.hyatt.com | pending / None |
| Cummins | https://cummins.jobs/columbus-in/cloud-network-engineer/38c5eb15293c46e394cef75f | https://cummins.jobs/columbus-in/cloud-network-engineer/38c5eb15 | active / nlx |
| House of Control (Teamtailor) | https://houseofcontrol.teamtailor.com/jobs/8310459-digital-marketing-specialist- | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Signicat (Teamtailor) | https://signicat.teamtailor.com/jobs/8370976-java-cloud-devops-engineer-readid | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| chatarmin (JOIN) | https://join.com/companies/chatarmin/16713707-key-account-customer-success-b2b-s | https://join.com | pending / None |
| NVISO (JOIN) | https://join.com/companies/nviso/16672486-information-security-resilience-senior | https://join.com | pending / None |
| BRANDAD (softgarden) | https://brandad.softgarden.io/job/14637273/Software%C2%ADentwickler-w-m-d-Schwer | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Patagonia (HireHive) | https://patagonia.hirehive.com/senior-director-finance-operations-fmd-amsterdam- | https://patagonia.hirehive.com | pending / None |
| Ulta Beauty | https://careers.ulta.com | https://careers.ulta.com/ | active / icims |
| REI Co-op | https://www.rei.jobs/jobs | https://www.rei.jobs/jobs | active / icims |
| Applied Materials | https://appliedmaterials.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Fortive | https://fortive.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| HP Inc. | https://hp.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Lam Research | https://lamresearch.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| SLB | https://slb.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Starbucks | https://starbucks.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |
| Whirlpool | https://whirlpool.eightfold.ai | – | **no row yet**: scan still `pending` in the worker queue (202 accepted); re-check |

## Known problems

| Issue | Detail | Status |
|---|---|---|
| CBRE and Jacobs `POST /jobs` returned 500 | Root cause (from the Cloud Run `api` logs): `echo_jobs._get_jobs` called `response.json()` on the HTML page CBRE/Jacobs return for the Echo Jobs probe, raising `JSONDecodeError` (a `ValueError`) that only `httpx.HTTPError` was caught for; it escaped `detect_embedded_ats_source` inside `register_discovered_board`. It was never Avature-specific. | **Fixed** by `59aa263` (catches `(httpx.HTTPError, ValueError)`), deployed. Both URLs were resubmitted afterwards and returned 202; their scans are queued behind the worker backlog (see below). |
| Eightfold job-URL detection gap | `<tenant>.eightfold.ai` job URLs only tried `[host, eightfold.ai]` as the tenant domain, so live job URLs landed as empty pending rows. | **Fixed** in `fcbf89f` (`?domain=` hint + `<tenant>.com` guess), pushed and deployed 2026-09-19. PayPal, Eaton, Boston Scientific, TriNet, GlobalFoundries and Lockheed are active Eightfold rows. Keurig Dr Pepper (`kdrp.eightfold.ai`) still doesn't resolve (its tenant domain isn't `kdrp.com`); it was pending at last check and may since have been rejected. |
| Scan worker is saturated (found 2026-09-19 17:16 UTC) | The `worker` Cloud Function (Pub/Sub `job-scan-requests`) is configured `maxInstanceCount=5`, concurrency 1, 540s timeout, and its logs show a steady stream of `The request was aborted because there was no available instance`. Adding ~600 boards in wave 4 made the dispatcher's first crawls enqueue a scan per discovered job, far more than 5 workers can drain. Effect: `POST /jobs` submissions stay `scan_status: pending` with **no crawl-source row** (board registration runs inside the scan worker since `59aa263`); ten wave 4 submissions (7 Eightfold tenants, 2 Teamtailor, 1 softgarden) were still in that state 1+ hour later. | Not lost: the eventarc subscriptions retry every 10s with no dead-letter limit and 24h retention, so the backlog drains on its own. To speed it up, raise the worker's `--max-instances` (watch the Cloud SQL connection limit, see the comment near line 23 of `deploy/gcloud-deploy.sh`). Not changed by this session. |
| Adding many boards floods the scan pipeline | Same cause: every new active board is crawled and each discovered job enqueues a scan. | Add large batches gradually, or raise worker capacity first. |
| Prod rate limiting | ~500 rapid writes triggered `Rate exceeded.` and timeouts on prod. Waves 3-4 used a paced writer with backoff and saw no 429s. | Recovered. |
| Adding many boards floods the scan pipeline | Each new active board is crawled by the dispatcher, which enqueues a scan per discovered job. | Expected; consider batching large additions. |
| Existing Greenhouse 404 rows (not from this work) | 10x Genomics, Allbirds, Applied Intuition, Aurora Innovation, Chewy (Fulfillment), Marqeta, Niantic, Opendoor, Rivian, plus `career4.successfactors.com` carried `last_error` 404s before this session. | Not touched. |

## Rows created by mistake (all fixed)

- `aexp.eightfold.ai` (American Express): created from a stale job ID; Amex has no working Eightfold API. **Deleted.**
- `paypal.eightfold.ai`, `eaton.eightfold.ai`, `bostonscientific.eightfold.ai`: created empty/pending, then **PATCHed to active Eightfold**. GlobalFoundries the same, after the fix deployed.

## Verify later

- **Shape-only boards** (never content-verified; check `last_error` after the first crawl): PNC, Citi, FIS, Seagate, Fannie Mae, Chegg, Atomi; the extra Workday tenants from the leftover pass (Brown University Health, Boston Medical Center, Kansas Health System, R1 RCM, Summit Health/CityMD, UVM Health Porter, Sonora Quest, General Mills, Tapestry, VF Corp, Constellation Brands, Kohl's, Belk, BioMarin, Genentech (Roche)); most wave 2-3 Workday boards (a search-result requisition URL only). The wave 4 Fortune 500 Workday and Oracle Fusion boards were checked more strongly (sitemap requisitions / `hcmRestApi` live counts, dated 8-19 Sept 2026).
- **Zero/near-zero posting boards added on request** in the leftover pass: Thinkific, Vacasa, Poshmark (Greenhouse), Kiddom, Docebo, Arcadia Science, Outpace Bio (0 jobs); Learneo, Mercari, Calm (1 job).
- **Names with unconfirmed ownership, review these:** Radiant, `Bare (BambooHR board)`, `JazzHR 'landing' board`, `Sphere (company unconfirmed)`, `Future (fitness)`, `Fetch (pet insurance)`, `Raya`, `Celsius (Workable)`, `Lunar (health-system software)`, `Cursor (flag for review)`, `Genesis AI` (medium confidence), `Personio (personio-gmbh board)` (may duplicate Personio's own board), `Tractor Supply (low confidence)`.
- **Possibly stale:** Gem boards Emerge Career (newest posting 2025-10), Black Ore (2025-08), Myriad Technology (2024-06); Vertex (Workday); UniUni and Alfil Logistics (Workable, counts never verified: the API 429'd).
- **Duplicate-company boards** (same company on two ATS boards, both added; watch for duplicate jobs): Secureframe (Ashby + Lever, different postings). Poshmark's live board is Ashby (the Greenhouse one is empty).
- **Other weak evidence:** Lockheed Martin resolved active as Eightfold although an early local test couldn't resolve its tenant (later resolved by the adapter fix); Dollar General's row was a `login-` iCIMS host; D.R. Horton's submitted job URL path was partly constructed (only the domain is stored); Genentech's pending row came from a dead (410) job URL.
- **Pending rows** need `implement-crawl-adapter` review. Several were since rejected or resolved (see the totals note above).

## Deliberately NOT added, wave 4 (nothing silently dropped)

- **Duplicate coverage skipped:** Cribl on Ashby (same 55 postings as the Greenhouse board added in wave 3); Qonto on Lever (Ashby board added instead); Applied Materials and HP Inc. on Workday (added on Eightfold instead, same jobs); Petco `wd504` host (the `wd1` board was added); Dick's Sporting Goods (identical URL in two forks); Toast on Greenhouse (already covered via a Clinch row); Snowflake's pending `careers.snowflake.com` row was left alone (the live Ashby board was added).
- **Zero postings, not added (playbook rule):**
  - Workable: healthcare-support-staffing-1, usa-healthcare-staffing-inc, host-healthcare, quorum, peoplepowered.
  - BambooHR: valneva, conservationmn, abortionfunds, douglascountyil.
  - JazzHR: yoursupportservicesnetwork, search, raptive, genalyte, surecost, skillcycle.
  - Gem: mission, gc-ai.
  - Personio: surein.
  - Breezy: awardspring, transact-campus, bluetread, resultstack, disruptive-advertising, localize, blue-orange-digital, predictionhealth, vosyn.
  - Ashby: bolt, bumble, consensys, figure, maven, mercury, reddit, solanalabs, stash, synctera, moneybox, truelayer, carbonhealth, lifestance, vast, raycast, stytch, ssi, turing, humanitec, valence, langfuse, readme, airtable, loom, vercel, beacons, billie, netease, tonies, opentable, function-health (Gem board added instead).
  - Lever: beam, form, gridmatic, mirror, pachama, carbonhealth, sesame, labelbox, teleport, pillar, imbue, pipedream, normalyze, clari, fitbod, whoop.
- **Dead or inactive accounts (search results are stale):** 15 closed BambooHR accounts that now redirect to bamboohr.com (metamaterialtechnologiesinc, amberkinetics, ambri, daavlin, gearboxsoftware, pittfoodpolicy, glitc, advancementproject, literacyaction, creativemindsetconsulting, ninetwothree, nvoicepay, simplecode, vitrum, knoxhr); JazzHR acrisure, tlc1, evotix (inactive), careerpage3 and jazzhrwhitelabel (vendor sandboxes); Personio tenants that 307 to `personio.com` (egym, pitch, receeve, gethorizon, hospitalitydigital, skopos-elements, gesellschaft-fuer-informatik, nvision-quantum-technologies); Personio `/xml` feed 404 (finapi-gmbh, finanzritter-gmbh).
- **Name collisions, skipped:** Ashby `sonder`, `levels`, `relay`, `scribe`, `cedar`, `capsule`, `sesame`, `flink`, `yotta`, `sanctuary`, `osmo`, `paradox`; Lever `alloy`, `neon`, `genesis`, `unify`, `latch`; Greenhouse `bethesda`, `remedy`, `vuori`, `ritual`; unidentified Ashby `arbor`, `vivid`, `dapper`, `swan`, `sunrise`.
- **Unsupported platforms found, no pending row created (no job URL fetched):** Teamtailor companies Salt, Varnish Software, Sofigate, Puzzel, Visma Software Nordic, EcoOnline, David Kennedy Recruitment; JOIN companies Software Engineering GmbH (`seg`), Nejo, IESF, fotograf.de, itestra; softgarden companies Karl Mayer, LV 1871, andrena, KBB; Wayfair and Noom (ATS not identified); AB InBev (`wd1.myworkdaysite.com/recruiting/abinbev`, 0 jobs, shape the adapter can't ingest).
- **Fortune 500 tenants not resolved (109 companies, guesses missed, not necessarily off Workday):** e.g. Regions, Discover, HSBC, MetLife, Aflac, Kroger, Publix, Walgreens, McDonald's, PepsiCo, ExxonMobil, Southern Co, Dominion, NextEra, Halliburton, Valero, RTX, General Dynamics, Union Pacific, CSX, Tesla, Honda, Kaiser, UPMC, Dell, AMD, NetApp, Western Digital. Wrong-company tenants rejected: `emerson.wd5` (Emerson College), `aa.wd105` (Auckland roles, not American Airlines), Fidelity's Oracle tenant (Fidelity Bank Ghana). Unidentified Oracle tenants skipped: `hcbt.fa.em2` (8,986 jobs), `ecnf.fa.us2`, `ejta.fa.us6`, `ehtl.fa.us6`, `eibd.fa.em2`; BHE returned 503.
- **No board at the slugs tried (other ATS or other slug):** ~550 fintech/health/climate slugs, ~370 AI/dev-tools slugs, and lifestyle/games names (Hydrow, Barry's, Noom, Olaplex, Funko, Ubisoft, Sega, Capcom, ...). The fork probe data was in the (temporary) scratchpad, not the repo.

## Earlier waves: looked at, not added

- **No board/domain found:** Lululemon, Ralph Lauren, Teladoc, Citizens Financial, Chubb (Oracle board added in wave 3), Apollo Education, Flatiron School, Hologic, Quest Diagnostics, Zimmer Biomet, Incyte, Charles River, Thermo Fisher, Enphase, Cummins (added as NLX), Emerson, Extreme Networks.
- **Name collisions, skipped:** Greenhouse `handshake`, `caribou`, `archer`; Intuitive Surgical's Workday tenant; Ascensus.
- **404 slugs and unsearched company lists** per wave are in the wave summaries in the chat transcript; none had a real board URL or domain to submit.

## Eightfold adapter fix (commit `fcbf89f`, deployed 2026-09-19)

`app/services/adapters/eightfold.py`: `_candidate_domains` takes the job URL's `?domain=` param as a hint (tried first) and, for `<tenant>.eightfold.ai` hosts, also tries `<tenant>.com`. Nine tests were added in `tests/services/adapters/test_eightfold.py` (six fail on the old code). Verified live on PayPal, Eaton, Boston Scientific, TriNet, GlobalFoundries and Lockheed Martin; Keurig Dr Pepper does not resolve.

## Wave 8 (2026-09-19, prod): gov / non-profit / legal-prof / staffing / insurance / agriculture / non-US

Prod went from 1,453 to 1,664 active (some of the difference is other sessions resolving pending rows). This wave created **208 active rows** plus 3 pending. Researchers were fresh general-purpose agents rather than forks (a fork inherits the prod token). Web search budget was never hit (about 115 searches across 7 agents). Writes were paced (0.4s, backoff): no 429s, no 5xx.

| Sector | Added active | Notes |
|---|---|---|
| Agriculture / food / environmental | 22 | ERM, Smithfield, Wayne-Sanderson, CGB, Primient on Workday; Halter, Apollo Agriculture etc. |
| Insurance | 29 | 17 Greenhouse/Lever/Ashby, 12 Workday |
| Non-profit / NGO / museums | 32 | GiveDirectly, ACLU, IRC, Ford Foundation, Met Museum, WFP, ... |
| Government / public higher-ed | 23 | 11 PeopleAdmin universities and CCDs, 12 Workday (states, counties, cities, RTD) |
| Staffing / recruiting | 29 | includes TrueBlue (PeopleReady) and Quess Corp on Oracle Fusion (Quess has ~2,500 reqs) |
| Legal / professional services | 33 | law firms and accountancies mostly on Workday (search-verified only) |
| Non-US platforms | 37 + 3 Pinpoint | 8 Gupy (Brazil), Personio, Recruitee, Workable, HireHive, Breezy, APAC/EU Workday, 2 Oracle Fusion |

**Problems found**
- **Personio adapter only matches `*.jobs.personio.de`** (`_PERSONIO_URL_RE` in `personio.py`); Ohpen, 1NCE and STARK are on `*.jobs.personio.com` and 422'd on the admin endpoint. Not added; an adapter fix would enable them.
- **Pinpoint is embedded-match-only**, so `POST /admin/crawl-sources` 422s on `*.pinpointhq.com` (same gotcha as SuccessFactors). The Premier League, Made Tech and London Hire Group were added by submitting a real posting URL taken from each board's `postings.json` through `POST /jobs`; all three resolved to active.
- **Clinch embedded fallback false-positives on NEOGOV**: submitting a governmentjobs.com and a schooljobs.com job URL created *active* `clinch` rows with the job URL as `board_url`. Both were PATCHed to `pending` with the domain root as `board_url`; their `ats_type` label still reads `clinch` (stale). Needs a NEOGOV adapter, or a Clinch guard.
- **Symetra** (`symetra.eightfold.ai`) 422'd: the tenant domain doesn't resolve (same class as Keurig Dr Pepper).
- **SmartRecruiters, Paylocity and Jobvite domains were already `rejected`** before this wave (created 13:36-15:19 UTC), so job-URL submissions for them (Frontier Agriculture, Mid Kansas Cooperative, Builders Mutual) created nothing. Rejected rows are excluded from the dedup list by the playbook, so the researchers proposed them again as "unsupported". Pending rows created: `ats.rippling.com`, `jobs.homerun.co`, `www.jobapscloud.com`.

**Not added (verify later or skip)**
- Board root only, no requisition found: Resolution Life, Jackson Financial, Tufts Health Plan, World Vision, Compassion International, Tony Blair Institute, Simpson Thacher, Alight, Port Houston, Tarrant Regional Water District, Las Vegas Valley Water District, City of Orlando, City of Vancouver, State of Nebraska, MUFG, Tyro.
- Requisition IDs look old / low confidence: Arch Group, Desjardins, King & Spalding, Onin Group, Forvis Mazars UK, Wonderbox.
- Marginal (one rolling "general interest" posting): Clean Crop Technologies.
- Many name-collision, zero-posting and 404 slugs are listed in the individual researcher reports (chat transcript only).
- Frankenmuth Insurance and Cross Country Healthcare (Dayforce): unverified, no adapter.

**Weakly verified, check `last_error` after first crawl**: all Workday/Oracle rows in this wave (search-surfaced requisition URLs only); Hitachi, Belron, RBA, Fugro (recency unconfirmed); Essity (no individual job URL, listing snippet only); ownership inferred from job content: Insurify, Hi Marley, Flock (UK), One Acre Fund, ACLU, Mercy For Animals, Openwork, Peddler, Emerson (Oracle Fusion; the earlier `emerson.wd5` tenant was Emerson College).

### Wave 8 follow-up: adapter fixes (uncommitted, not yet deployed)

- `personio.py`: `_PERSONIO_URL_RE` now matches `*.jobs.personio.com` as well as `.de` (both serve the same `/xml` feed; still fetched/stored as `.de`, so both TLDs collapse to one board key). Unblocks Ohpen, 1NCE, STARK, which 422'd in wave 8. Tests: `tests/services/adapters/test_personio.py`.
- `pinpoint.py`: added a static `match` for hosted `<slug>.pinpointhq.com` boards so `POST /admin/crawl-sources` works directly (custom-domain boards still go through `embedded_match`). Tests: `tests/services/adapters/test_pinpoint_clinch.py`.
- `clinch.py`: the sitemap *fallback* used by `detect_embedded_ats_source` now requires the sitemap's `/jobs/` URLs to be on the same host and single-segment (`/jobs/{slug}`), which stops NEOGOV (`governmentjobs.com` and `schooljobs.com`, whose sitemap is governmentjobs.com's) from registering as active `clinch` rows. Crawling (`_fetch_jobs`) is unchanged on purpose: existing iCIMS-hosted rows labeled `clinch` use `/jobs/{id}/{slug}/job`. Verified live: NEOGOV -> None; careers.upstart.com and careers.toasttab.com still -> clinch. Note existing false-positive `clinch` rows (Teamtailor `houseofcontrol`, `signicat`; ApplicantPro `darlingii`; three iCIMS tenants) are untouched.

## Wave 9 (2026-09-19, prod): 7 more sectors, 343 boards, all 201

Prod went to **2,009 active** (from 1,453 at the start of waves 8-9). No 422/409/5xx on any of the 343 writes. The dedupe list now included rejected rows, and the unsupported-platform bucket was made optional (SmartRecruiters, Jobvite, Paylocity, Taleo, UltiPro, NEOGOV, Rippling, Homerun, JobAps are already evaluated), so no new pending rows this wave.

| Sector | Added active | Notes |
|---|---|---|
| Space / defense / robotics / hardware | 37 | Archer (`archer56`; bare `archer` is a vet clinic), Oklo, Neros, Vast, Aerospace Corp, AeroVironment, RTX/Collins, Airbus |
| Marketing / adtech / media | 42 | Klaviyo, HubSpot (`hubspotjobs`), Trade Desk, NYT, dentsu, Havas, FOX |
| Mining / materials / chemicals / midstream / waste | 47 | Workday-heavy (Albemarle, Mosaic, Alcoa, Shell, Republic Services), plus Clean Harbors on Oracle |
| Healthcare providers | 62 | Behavioral health, vet groups, senior living, Planned Parenthood affiliates, ~25 Workday health systems, 3 Oracle |
| Construction / engineering | 55 | Workable/Greenhouse contractors, ~20 Workday firms, WSP on Oracle |
| Crypto / web3 / AI infra | 44 | market makers (IMC, Akuna), exchanges (OKX, Bitpanda), Ashby protocol teams |
| Restaurants / fitness / sports / leisure | 56 | Teams (Monumental, Panthers, NFL), coffee chains, Carnival and ClubCorp on Oracle |

**Held back:** low req numbers or root only: Jefferson Health, AltaMed, Residential Home Health & Hospice (tenant shared with Kaplan), Mortenson, SOM (intern reqs only), Gensler ("Career Site is Moving"), Primoris, Chemours (host unclear), Ashland, Mitsubishi Chemical, CANPACK, Leonardo (owner unconfirmed), Anaheim Ducks (2024 IDs); 1-2 posting boards: Via Separations, Resonant Energy, Philadelphia Eagles, 0x, Subzero; other: M+A (`+` in slug), Solana Foundation (space in Ashby slug), Arc'teryx and Beauty Barrage (off-sector), Bedrock Robotics and Factorial Energy (already added in an earlier batch this wave).

**Crawl state at 20:40 UTC:** only 8 of the 551 rows created in waves 8-9 had been crawled (0 errors), so the Workday/Oracle rows above are still unconfirmed beyond a search-surfaced requisition URL.

### Finding (2026-09-19 ~20:50 UTC): crawl-worker is undersized for 2,009 active sources

Why only 8 of 551 new boards had been crawled: the dispatcher enqueues every active source hourly, but `crawl-worker` is `maxScale=3` with concurrency 1 and a successful crawl averages 7.2s (p50 1.2s, p90 18.5s, max 156s), so it clears ~1,400-1,500 sources/hr. At 1,453 active that was about break-even; at 2,009 it isn't. The eventarc subscription's backlog grows every hour (oldest unacked ~4.7h, ~21,000 429 "no available instance" responses/hr as Pub/Sub retries), and new boards wait behind the FIFO backlog. Not a bug in the adapters. Fix options: raise `crawl-worker --max-instances` (~8 gives ~2x headroom, +5 DB connections) and/or slow the hourly scheduler. Also: the pull subscription `crawl-source-requests-worker` has ~10k unconsumed messages in prod (no prod consumer; recreated by the dispatcher if deleted), so ignore it when reading backlog metrics.

**Applied 2026-09-19 21:33 UTC (prod):** `crawl-worker` `--max-instances` 3 -> 8 (revision `crawl-worker-00049-zfh`) and Scheduler job `crawl-dispatch-hourly` schedule `0 * * * *` -> `0 */2 * * *` (the job's name is now a misnomer). `deploy/gcloud-deploy.sh` updated to match (uncommitted). CI's `gcloud functions deploy crawl-worker` passes no `--max-instances`, so the live value persists across pushes. Five minutes later: instances ramped 3 -> 8, eventarc backlog 2,596 -> 2,287 and falling, DB backends peaked at 48. Expected: ~4,000 crawls/hr capacity vs ~1,000/hr demand (2,009 sources every 2h).
