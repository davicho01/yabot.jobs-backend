# Crawl-source discovery ledger

Record of `/discover-crawl-sources` work against prod (`https://api.yabot.jobs`), 2026-09-19. Status and ATS columns are generated from the live `GET /admin/crawl-sources` roster, so they show what prod actually holds, not what was intended. Per-request outcomes for the wave 3 writes are in `docs/crawl-source-ledger.jsonl`.

**Prod totals at last update:** 709 active, 53 pending, 10 rejected (session start: 191 / 11 / 10).

**How to read the tables:** *Board URL* is the literal URL submitted. **shape-only** means a Workday/Lever/etc. board added through `POST /admin/crawl-sources`, which only checks URL shape. Workday's SPA can't be fetched, so those boards were never content-verified: a wrong tenant shows up as a `last_error` after the first crawl. `pending` rows are new platforms or domains with no adapter yet (the queue for `implement-crawl-adapter`).


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
| PayPal | https://paypal.eightfold.ai/careers | – | not added here | Eightfold root 422s on the admin endpoint (no static URL shape); handled below via /jobs + PATCH |
| American Express | https://aexp.eightfold.ai/careers | – | not added here | Eightfold root 422'd; Amex has no working Eightfold API. Placeholder row later deleted |
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

## Submitted via `POST /jobs` (job-URL / domain submissions)

Unsupported platforms land as `pending` keyed by domain. Supported or embedded platforms resolve to `active`, sometimes on a different board than the submitted URL (e.g. Cleveland Clinic and Palo Alto Networks resolved to their Workday boards).

| Company | Submitted | Row in prod | Status / ATS |
|---|---|---|---|
| PayPal | https://paypal.eightfold.ai/careers/job/274904264573 | https://paypal.eightfold.ai | active / eightfold |
| American Express | https://aexp.eightfold.ai/careers/job/38842605 | (placeholder deleted) | – |
| USAA | https://www.usaajobs.com/job/san-antonio/senior-infrastructure-engineer-data-pro | https://usaa.wd1.myworkdayjobs.com/USAAJOBSWD | active / workday |
| Liberty Mutual | https://searchjobs.libertymutualgroup.com/careers/job/618519419340 | https://searchjobs.libertymutualgroup.com | pending / None |
| Fidelity | https://jobs.fidelity.com/en/jobs/2134523/director-data-platform-mainframe-devel | https://jobs.fidelity.com | pending / None |
| Synopsys | https://synopsys.avature.net/careers/JobDetail/Validation-Verification-Eng-Sr-En | https://synopsys.avature.net | pending / None |
| Heitman | https://jobs.jobvite.com/heitman/job/oGgyAfwO | https://jobs.jobvite.com | pending / None |
| McGraw Hill | https://careers.mheducation.com/jobs/6545 | https://careers.mheducation.com | pending / None |
| Ellucian | https://careers.ellucian.com/jobs/6309 | https://careers.ellucian.com | pending / None |
| CBRE | https://careers.cbre.com/en_US/careers/JobDetail/268297 | **no row** (500, see Problems) | – |
| Rocket Companies | https://careers.rocket.com/careers/r-081326/capital-markets-associate/ | https://careers.rocket.com | pending / None |
| D.R. Horton | https://drhorton.taleo.net/careersection/2/jobdetail.ftl?job=2602082 | https://drhorton.taleo.net | pending / None |
| Texas Instruments | https://careers.ti.com/en/sites/CX/job/25009893 | https://careers.ti.com/en/sites/CX/job/25009893 | active / oracle_fusion |
| Lattice Semiconductor | https://careers-latticesemi.icims.com/jobs/3476/applications-eng-3/job | https://careers-latticesemi.icims.com | pending / None |
| Keysight | https://careers-keysight.icims.com | https://careers-keysight.icims.com | pending / None |
| PowerSchool | https://careers3-powerschool.icims.com | https://careers3-powerschool.icims.com/ | active / clinch |
| Goldman Sachs | https://higher.gs.com | https://higher.gs.com | pending / None |
| Qualcomm | https://careers.qualcomm.com | https://careers.qualcomm.com | active / eightfold |
| AbbVie | https://careers.abbvie.com/en/job/senior-scientist-i-in-worcester-ma-jid-31706 | https://careers.abbvie.com | pending / None |
| Eaton | https://eaton.eightfold.ai/careers/job/687238289031-lead-engineer-systems-engine | https://eaton.eightfold.ai | active / eightfold |
| TireHub (UKG) | https://recruiting.ultipro.com/HAW1005HAWNE/JobBoard/fb14a429-ca54-48db-b7e0-0c5 | https://recruiting.ultipro.com | pending / None |
| UnitedHealth Group | https://careers.unitedhealthgroup.com/job/eden-prairie/senior-software-engineer- | https://careers.unitedhealthgroup.com/job/eden-prairie/senior-so | active / talentbrew |
| Larry H. Miller Senior Health (Paylocity) | https://recruiting.paylocity.com/recruiting/jobs/Details/4430542/Larry-H-Miller- | https://recruiting.paylocity.com | pending / None |
| Boston Scientific | https://bostonscientific.eightfold.ai/careers/job/563602813281108-senior-ai-solu | https://bostonscientific.eightfold.ai | active / eightfold |
| Under Armour | https://careers.underarmour.com/job/Remote-Sr_-Product-Manager-Analytics-and-Dat | https://careers.underarmour.com/job/Remote-Sr_-Product-Manager-A | active / successfactors |
| The Hershey Company | https://careers.thehersheycompany.com/job/Hershey-Production-Operator-Reese-Plan | https://careers.thehersheycompany.com/job/Hershey-Production-Ope | active / successfactors |
| Mattel | https://jobs.smartrecruiters.com/mattelinc/744000146081909-mattel-retail-team-as | https://jobs.smartrecruiters.com | pending / None |
| Genentech | https://careers.gene.com/us/en/job/ | https://careers.gene.com | pending / None |
| HCA Healthcare | https://careers.hcahealthcare.com/jobs/16648854-sales-and-use-tax-intern | https://careers.hcahealthcare.com | pending / None |
| Cleveland Clinic | https://jobs.clevelandclinic.org | https://ccf.wd1.myworkdayjobs.com/ClevelandClinicCareers | active / workday |
| Mayo Clinic | https://jobs.mayoclinic.org | https://jobs.mayoclinic.org/ | active / talentbrew |
| Colgate-Palmolive | https://jobs.colgate.com | https://jobs.colgate.com/ | active / successfactors |
| Electronic Arts | https://ea.gr8people.com | https://ea.gr8people.com | pending / None |
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
| Jacobs | https://careers.jacobs.com | **no row** (500, see Problems) | – |
| AECOM | https://aecom.jobs | https://aecom.jobs/ | active / nlx |
| Sandia National Labs | https://sandia.jobs | https://sandia.jobs | pending / None |
| Harvard University | https://careers.harvard.edu | https://careers.harvard.edu | pending / None |
| Stanford University | https://careersearch.stanford.edu | https://careersearch.stanford.edu | pending / None |
| Johns Hopkins University | https://hiring.jhu.edu | https://hiring.jhu.edu | active / eightfold |
| Eightfold AI | https://app.eightfold.ai/careers/job/68763888174 | https://app.eightfold.ai | active / eightfold |
| ServiceNow | https://jobs.smartrecruiters.com/servicenow/744000149961559-staff-software-engin | https://jobs.smartrecruiters.com | pending / None |
| IBM | https://careers.ibm.com/en_US/careers/JobDetail/Software-Developer-Intern-2027/1 | https://careers.ibm.com | pending / None |
| SAP | https://jobs.sap.com | https://jobs.sap.com/ | active / successfactors |
| Palo Alto Networks | https://jobs.paloaltonetworks.com/en/job/santa-clara/principal-engineer-software | https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcaree | active / workday |
| State Farm | https://careers-statefarm.icims.com | https://careers-statefarm.icims.com | pending / None |
| Progressive | https://careers.progressive.com/jobs/17648069-medical-claims-representative-trai | https://careers.progressive.com | pending / None |
| TriNet | https://trinet.eightfold.ai | https://trinet.eightfold.ai | active / eightfold |
| GlobalFoundries | https://globalfoundries.eightfold.ai | https://globalfoundries.eightfold.ai | pending / None |
| Deloitte (Belgium/Avature) | https://deloittebe.avature.net/en_US/careers/JobDetail/SAP-Supply-Chain-Project- | https://deloittebe.avature.net | pending / None |
| Kelly Services | https://jobs.smartrecruiters.com/PartneredStaffing-KellyServices/743999652752715 | https://jobs.smartrecruiters.com | pending / None |
| Wolters Kluwer (SmartRecruiters) | https://jobs.smartrecruiters.com/WoltersKluwer1/83235277-application-support-spe | https://jobs.smartrecruiters.com | pending / None |
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

## Known problems (open)

| Issue | Detail | Status |
|---|---|---|
| CBRE `POST /jobs` returns 500 | `https://careers.cbre.com/en_US/careers/JobDetail/268297` (Avature, `cbreglobal.avature.net` redirects here). Returned `500 Internal Server Error` on 3 separate attempts, so it is deterministic. No row exists for CBRE. | Uninvestigated. Saved as a project memory. |
| Eightfold job-URL detection gap | For `<tenant>.eightfold.ai` hosts, `_candidate_domains` in `app/services/adapters/eightfold.py` only tries the host and `eightfold.ai` as the tenant domain. The real domain (`paypal.com`, `eaton.com`, ...) is in the job URL's `?domain=` query, never read. A live job URL therefore lands as an empty `pending` row. | Worked around for PayPal, Eaton and Boston Scientific with `PATCH /admin/crawl-sources/{id}` (bare `board_url`, `status: active`). Adapter fix not made. |
| Prod rate limiting | ~500 writes in one session triggered `Rate exceeded.` and timeouts on prod. Later batches use `add2.py`-style pacing with backoff. | Recovered on its own. |
| Existing Greenhouse 404 rows (not from this work) | 10x Genomics, Allbirds, Applied Intuition, Aurora Innovation, Chewy (Fulfillment), Marqeta, Niantic, Opendoor, Rivian, plus `career4.successfactors.com` carry `last_error` 404s from before this session. | Not touched. |

## Placeholder rows created by mistake (fixed)

- `aexp.eightfold.ai` (American Express): created from a stale job ID; Amex has no working Eightfold API. **Deleted.**
- `paypal.eightfold.ai`, `eaton.eightfold.ai`, `bostonscientific.eightfold.ai`: created empty/pending, then **PATCHed to active Eightfold**.

## Verify later

- **Shape-only boards** (never content-verified; check `last_error` after the first crawl): PNC, Citi, FIS, Seagate, Fannie Mae, Chegg, Atomi; the extra Workday tenants (Brown University Health, Boston Medical Center, Kansas Health System, R1 RCM, Summit Health/CityMD, UVM Health Porter, Sonora Quest, General Mills, Tapestry, VF Corp, Constellation Brands, Kohl's, Belk, BioMarin, Genentech (Roche)); and most other Workday boards, which rest on a search-result requisition URL only.
- **Zero/near-zero posting boards added on request** (playbook normally skips these): Thinkific, Vacasa, Poshmark, Kiddom, Docebo, Arcadia Science, Outpace Bio (0 jobs); Learneo, Mercari, Calm (1 job).
- **Weak evidence:** Lockheed Martin resolved active as Eightfold although a local `_fetch_jobs` test failed to resolve its tenant; Dollar General's pending row is a `login-` iCIMS host; D.R. Horton's submitted job URL path was partly constructed (only the domain is stored); Radiant's company name is unconfirmed; Vertex (Workday) may be a stale tenant; Genentech pending row came from a dead (410) job URL, only the domain is meaningful.
- **Pending rows** need `implement-crawl-adapter` review: they are new platforms or domains with no adapter yet.

## Looked at, NOT added (nothing silently dropped)

No usable board URL or domain was found for these, so nothing real could be submitted.

- **No board/domain:** Lululemon, Ralph Lauren, Teladoc (Workday site name unknown, probably migrated), Citizens Financial, Chubb, Apollo Education Systems and Flatiron School (no Greenhouse board at any tested slug), Hologic, Quest Diagnostics, Zimmer Biomet, Incyte (no Workday board found), Charles River, Thermo Fisher, Enphase, Cummins, Emerson (wrong-company search hits), Extreme Networks (Jobvite, zero jobs; covered by the shared Jobvite pending row), extra UKG boards (Meritus, Sheppard Pratt, Valley View, Independence Health, Monadnock, CTDI, Comprehensive Logistics; all collapse into the single `recruiting.ultipro.com` pending row).
- **Name collisions, deliberately skipped:** Greenhouse `handshake` (unrelated consulting firm), Greenhouse `caribou` (car-loan fintech, not the biotech), Greenhouse `archer` (veterinary clinic), Intuitive Surgical Workday tenant (belongs to Intuitive Research & Technology), Ascensus (not healthcare).
- **Slugs that 404'd at the guessed slug (company probably on another platform or slug):** long lists per sector; see the sector sections of the chat transcript. Notable: Ripple, Plaid, Paxos, Groq, Zipline, SoundCloud, Etsy, DraftKings, Warner Music Group, Fanatics, Whatnot, Benchling, insitro, Grail, Sarepta, Neurocrine, Hims, Cedar, Zus, Innovaccer.
- **Not searched (out of budget/scope in their wave):** Danaher, J&J, AstraZeneca, Tesla, Sierra Space, Firefly, GE Vernova, Deere, Textron, Parker Hannifin, Johnson Controls, XPO, Expeditors, FedEx, UPS, Kellanova, Molson Coors, Smucker, Macy's, PepsiCo, Coca-Cola, Paramount, Fox, Disney, Nexstar.

## Wave 3 notes

- **Not added / looked at, wave 3:** the 404-slug lists, wrong-company collisions (Ashby `ladder` and `slate`, Greenhouse `raven`/`liftoff`, Lever `factor`, Intapp's stale tenant), zero-posting boards (HubSpot, Vercel, Mistral, several travel/mobility slugs) and the unsearched company lists are in the fork reports in the chat transcript; none had a real board URL or domain to submit.
- **Duplicate coverage:** Wayve is live on both Greenhouse (`job-boards.greenhouse.io/wayve`, 192) and Ashby (189); only the Ashby board was added (newer). Add the Greenhouse one if you want both.
- **Cursor (Ashby `cursor`)** was added as "Cursor (flag for review)": the description says "automate coding" but the fetch summary named the company "SpaceXAI".
- **GlobalFoundries** (`globalfoundries.eightfold.ai`, 522 jobs) and **Keurig Dr Pepper** (`kdrp.eightfold.ai`, 500 jobs) are pending Eightfold rows. The adapter fix (see below) resolves GlobalFoundries once deployed; KDP still needs its real tenant domain.
- **Second CBRE-style 500:** `POST /jobs {"url": "https://careers.jacobs.com"}` also returned a bare 500 (no row for Jacobs).

## Eightfold adapter fix (uncommitted, not yet deployed)

`app/services/adapters/eightfold.py`: `_candidate_domains` now takes the job URL's `?domain=` param as a hint (tried first) and, for `<tenant>.eightfold.ai` hosts, also tries `<tenant>.com`. Tests added in `tests/services/adapters/test_eightfold.py` (6 of the 9 new tests fail on the old code). Live check against real tenants: PayPal, Eaton, Boston Scientific, TriNet, GlobalFoundries and Lockheed Martin all resolve with and without `?domain=`; KDP does not (its tenant domain isn't `kdrp.com`).
