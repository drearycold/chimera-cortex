# RAG Benchmark QA Pairs — Cross-Chunk (Fate/Grand Order Lore)

> **Source:** Character lore documents under `/Users/peterlee/git/chimera-cortex/documents/`  
> **Question Type:** Multi-hop / Cross-chunk inference · **Difficulty:** Medium  
> **Format:** Question / Answer / Required Source Chunks / Source Files  
> **Note:** No single passage is sufficient to answer these questions. The RAG system must retrieve and synthesize multiple chunks.

---

## QA-11

**Question:** What abilities did Scáthach teach Cú Chulainn, and how does Cú Chulainn make use of those teachings differently when summoned as a Lancer versus a Caster?

**Answer:**  
Scáthach taught Cú Chulainn "all sorts of techniques," including Rune magic and martial arts, and also bestowed upon him her beloved spear (Gáe Bolg).  
- As a **Lancer**: He focuses on spear combat — Gáe Bolg is his primary Noble Phantasm. Although he possesses Runes at Rank B (taught by Scáthach), he finds them a nuisance and keeps them sealed away.  
- As a **Caster**: With his spear sealed, he shifts entirely to Rune spellcraft (Rank A), specializing in fire and heat attacks via the "Ansuz" runic letter. He frequently laments in battle that fighting without his spear is a pain.

**Required Chunk 1** — `070_Scáthach_lore.md` (Lines 26–27):
> "Later she became the mentor of young Cú Chulainn — who would later grow into the hero of Ulster, teaching him all sorts of techniques and even bestowing upon him her beloved spear."

**Required Chunk 2** — `017_Cú_Chulainn_lore.md` (Lines 59–63):
> "Runes: B. He possesses Northern European magical crests called Runes... This is the way his mentor Scáthach thought, so Cú Chulainn is well-versed in Rune spells. He is close to being a top tier rune user but he thinks the Runes just get in the way, so he does not use them often."

**Required Chunk 3** — `038_Cú_Chulainn_lore.md` (Lines 46–48):
> "Rune Spell: A. He possesses Northern European magical crests called Runes, given to him by Scáthach. By using them, he can create powerful and diverse effects. However, he cannot use multiple Runes at the same time."

**Source Files:** `070_Scáthach_lore.md` × `017_Cú_Chulainn_lore.md` × `038_Cú_Chulainn_lore.md`

---

## QA-12

**Question:** Who created Mordred and for what purpose? And despite that origin, what was Mordred's actual attitude toward Altria?

**Answer:**  
Mordred was created by the witch Morgan — Altria's elder sister and mortal enemy — as a homunculus. Morgan's intent was for Mordred to kill Arthur and usher in an even greater king. However, Mordred did not share Morgan's hatred toward Altria. Instead, Mordred deeply admired Arthur and desired nothing more than the king's acceptance. It was only after Altria's rejection that Mordred turned against the king and set Britain on a path to ruin.

**Required Chunk 1** — `076_Mordred_lore.md` (Lines 38–40):
> "Mordred is an artificial creation, a type of homunculus, created by the witch Morgan who is the elder sister and mortal enemy of King Arthur. Morgan's purpose in creating Mordred was the death of Arthur and the rise of an even greater king."

**Required Chunk 2** — `076_Mordred_lore.md` (Lines 49–51):
> "Despite Morgan's plans, Mordred admired Arthur greatly and wanted the king's acceptance more than anything, but all that changed with King Arthur's rejection. Mordred began to plot Arthur's destruction and the end of his glory."

**Required Chunk 3** — `002_Altria_Pendragon_lore.md` (Lines 67–69):
> "King Arthur repelled many foreign enemies, but was unable to save Britain. Due to the betrayal of Mordred, one of the Knights of the Round Table, the country was split into two and the Knights' castle Camelot lost its gleam."

**Source Files:** `076_Mordred_lore.md` × `002_Altria_Pendragon_lore.md`

---

## QA-13

**Question:** What happened to Altria Pendragon at the Battle of Camlann, and what role did Bedivere play in her final moments?

**Answer:**  
Altria defeated Mordred at the Hill of Camlann but suffered fatal wounds. With her last strength, she entrusted Excalibur to Bedivere — her final confidante — and departed the human realm, eventually arriving in Avalon. However, in this work's setting, Bedivere never returned the sword. Altria died without knowing whether Bedivere had fulfilled her wish. Consumed by guilt, Bedivere kept himself alive for centuries through sheer force of will, until he finally exhausted himself in Avalon.

**Required Chunk 1** — `002_Altria_Pendragon_lore.md` (Line 73):
> "King Arthur defeated Mordred on the Hill of Camlann, but she suffered fatal wounds and fell to her knees. Before her last breath, she entrusted her sacred sword to her last confidante Bedivere, and left the human realm. After death, she was taken to utopia... Avalon, a paradise that exists beyond this realm."

**Required Chunk 2** — `126_Bedivere_lore.md` (Line 45):
> "In this work, he is an existence 'as if Bedivere did not return King Arthur's sacred sword.' King Arthur died without knowing whether Bedivere had returned the sword. Out of that guilt, Bedivere's desire to finally return the sword kept him alive for centuries, until he ran out of strength in Avalon."

**Source Files:** `002_Altria_Pendragon_lore.md` × `126_Bedivere_lore.md`

---

## QA-14

**Question:** What is the true identity of Bedivere's Silver Arm "Airgetlám," and what makes Bedivere unique among the Knights of the Round Table?

**Answer:**  
Despite sharing its name with the arm of the Celtic God of War Nuadha, Airgetlám's true identity is the sacred sword Excalibur — the very sword Bedivere failed to return. When its True Name is released as "Dead End — Airgetlám," it can perform an Anti-Army level attack. As for his place among the Round Table: Bedivere is the only ordinary human among the assembled superhuman knights. He has no special bloodline or divine heritage, yet stood among them as a brilliant general and skilled swordsman. He is notably not a Heroic Spirit, but merely a man from the past.

**Required Chunk 1** — `126_Bedivere_lore.md` (Lines 53–58):
> "『Switch On - Airgetlám』... the silver arm that shares the same name as the divine weapon used by the Celtic God of War, but its true identity is 'the sacred sword Excalibur that he failed to return.'"

**Required Chunk 2** — `126_Bedivere_lore.md` (Lines 39–41):
> "Within the Round Table where superhuman heroes gathered, he was the one and only normal human to serve King Arthur. Though one-armed, it is said he was a brilliant general and a knight with superior swordsmanship. However, he is not a Heroic Spirit, but merely a man from the past."

**Required Chunk 3** — `126_Bedivere_lore.md` (Bond Level 4 dialogue):
> "My Silver Arm, Airgetlám. I received it from the great mage Merlin. It is said to be the arm of the Celtic God of War, Nuadha. Having only one arm, I requested this power in order to keep up with the other powerful knights."

**Source File:** `126_Bedivere_lore.md` (Profile section + Bond dialogue section — cross-section, single file)

---

## QA-15

**Question:** What is the relationship between Karna and Arjuna, and why were they destined to become enemies?

**Answer:**  
Karna and Arjuna are half-brothers — both born of the same mother, Kunti, but from different divine fathers (Karna's from Surya the Sun God; Arjuna's from Indra the Thunder God). Karna was abandoned at birth and raised as a coachman's son, later joining the Kuru family — the rivals of Arjuna's Pandava clan. Their enmity, however, was not divinely ordained. According to Arjuna's own lore, the decision to kill Karna was made by Arjuna himself the moment they first met — a karma chosen "with pure malicious intent," not fate.

**Required Chunk 1** — `085_Karna_lore.md` (Lines 39–41):
> "Karna was born of Kunti, a human girl, and Surya, the God of Sun. After Karna's birth, he was abandoned by his mother Kunti. He was then adopted by a coachman, and raised as the man's own son."

**Required Chunk 2** — `085_Karna_lore.md` (Lines 46–48):
> "After Kunti abandoned Karna, she gave birth to the five brothers of the royal Pandu family. The third brother, Arjuna, would grow up to be Karna's rival. Karna was later adopted into the Kuru family, the rival of the Pandu family."

**Required Chunk 3** — `084_Arjuna_lore.md` (Lines 39–40):
> "He was born the son of King Kuru, the third of the five Pandava brothers, and also the son of the Lightning God Indra."

**Required Chunk 4** — `084_Arjuna_lore.md` (Lines 57–61):
> "When did he decide to kill Karna? Probably when they first met. This was not a fate decided by the gods. It is a karma that Arjuna chose with pure malicious intent. Even if it is not a righteous one, Arjuna must walk that path."

**Source Files:** `085_Karna_lore.md` × `084_Arjuna_lore.md`

---

## QA-16

**Question:** Why is Scáthach unable to die, and what does she wish from the Holy Grail?

**Answer:**  
Scáthach transcended humanity by killing a god, which placed her "outside" of the world's framework. As a result, she lost the capacity to die — she is neither a true Heroic Spirit nor a Divine Spirit, belonging to neither the living nor the dead. She can only continue to exist until everything in the world disappears. Rather than accepting this state, Scáthach desperately desires death. Her wish to the Holy Grail is for it to send someone capable of killing her — and if possible, that the person be the one to whom she once entrusted her spear (i.e., Cú Chulainn).

**Required Chunk 1** — `070_Scáthach_lore.md` (Lines 41–43):
> "Wisdom of Dún Scáith: A+. Because she has surpassed humanity, killed a god, and placed herself 'outside' of the world, she is able to use all skills at the rank of B to A..."

**Required Chunk 2** — `070_Scáthach_lore.md` (Lines 71–75):
> "After so long, Scáthach has finally ascended to the level of half a Divine Spirit. She cannot die a beautiful death, nor can she die an ugly one. All she can do is continue to exist until the world, both inside and out, disappears. If the Grail is truly almighty, send someone who can actually kill me. Also, if possible, let that someone be the possessor of the spear I once bestowed with my own hands..."

**Required Chunk 3** — `070_Scáthach_lore.md` (Bond Level 5 dialogue):
> "I desire death. If the Holy Grail truly possess omnipotent power, I would ask it to send someone capable of killing me. And if possible I pray that the person it sends be the one whom I once bestowed this spear to."

**Source File:** `070_Scáthach_lore.md` (Profile section + Bond dialogue section — cross-section, single file)

---

## QA-17

**Question:** How does Karna view Gilgamesh, and how does that perspective reflect on Karna's own circumstances?

**Answer:**  
Karna acknowledges Gilgamesh as a true king — praising his unwavering arrogance, his absolute confidence, and his rule over a nation of stalwart people. Karna even admits he is "a bit jealous." This stands in poignant contrast to Karna's own situation: despite possessing martial caliber and personal dignity ranked among the highest of all Heroic Spirits, his worth was never publicly recognized in life due to prejudice against his lowly birth. He was denied the status of a king, raised as an adoptive son of a coachman, and his valor went unacknowledged. His admiration for Gilgamesh implicitly reveals his longing for recognition.

**Required Chunk 1** — `085_Karna_lore.md` (Conversation 7):
> "The golden man, huh? His arrogance never falters, and he never doubts his decisions. I may not care for him, but I certainly must concede that he is a true king. Surely he must have ruled over a strong nation with stalwart citizens. I'm a bit jealous about that."

**Required Chunk 2** — `085_Karna_lore.md` (Lines 69–71):
> "Karna accepts everything as 'it happens,' and is a Servant who's extremely generous. He treats everyone equally, and respects everyone equally. Although it was never publicly acknowledged due to prejudice, Karna's caliber as a martial artist and dignity as a person can be ranked one of the highest amongst all Servants."

**Source File:** `085_Karna_lore.md` (Profile section + My Room dialogue section — cross-section, single file)

---

## QA-18

**Question:** What thematic similarities exist between Merlin's Noble Phantasm "Garden of Avalon" and Mash's Noble Phantasm "Lord Camelot"? How do they differ in type and function?

**Answer:**  
Both Noble Phantasms draw on the mythology of the Arthurian utopia — Avalon and Camelot respectively — and both are fundamentally protective rather than offensive.  
- **Garden of Avalon** (Anti-Personnel, Rank C): Merlin recreates the tower in which he is sealed. Even in the darkest hell, flowers bloom and sunlight fills the surroundings. Its essence is hope — "The place where Merlin dwells will never be a hell."  
- **Lord Camelot** (Anti-Evil, Rank B+++): Galahad's Noble Phantasm, an ultimate defensive barrier using the Round Table at Camelot's center as its foundation. Its power scales with the user's heart — "As long as the heart does not falter, nor shall this wall of defense."  
Both are support/defense Noble Phantasms sharing the Arthurian sacred-land aesthetic, and both link inner spiritual strength to their power output.

**Required Chunk 1** — `150_Merlin_lore.md` (Lines 57–65):
> "『Garden of Avalon』 Rank: C Type: Anti-Personnel — Eternally Secluded Utopia. Merlin recreates the tower where he is still sealed away. Flowers bloom on the ground, and warm sunlight pours into the surroundings, even in the darkest depths of hell... The place where Merlin, the Mage of Flowers, dwells will never be a hell. It will be a land filled with hope."

**Required Chunk 2** — `001_Mash_Kyrielight_lore.md` (Lines 46–53):
> "『Lord Camelot』 Rank: B+++ NP Type: Anti-Evil — Castle of the Distant Utopia. Noble Phantasm of the Heroic Spirit Galahad. It's an ultimate defense, utilizing the table located in the center of Camelot... Its strength is proportional to the user's heart. As long as the heart does not falter, nor shall this wall of defense."

**Source Files:** `150_Merlin_lore.md` × `001_Mash_Kyrielight_lore.md`

---

## QA-19

**Question:** Both Cú Chulainn and Scáthach possess a Noble Phantasm called "Gáe Bolg." What are the key differences between the two?

**Answer:**  
Despite the shared name, the two weapons are distinct:  
- **Cú Chulainn's Gáe Bolg** (Rank B, Anti-Personnel): Operates by reversing cause and effect — the spear is thrust *after* it has already pierced the target's heart, making it theoretically undodgeable. It is a cursed spear that always targets the heart.  
- **Scáthach's Gáe Bolg Alternative** (Rank B, Anti-Personnel): Visually similar, but it is the older original model. Rather than a single spear, it consists of two spears fired simultaneously.

**Required Chunk 1** — `017_Cú_Chulainn_lore.md` (Lines 39–45):
> "『Gáe Bolg』 Rank: B NP Type: Anti-Personnel — Barbed Spear that Pierces with Death. The cursed spear that always pierces the target's heart. The secret is that this spear is able to reverse cause and effect, so that the spear is thrust AFTER it already pierces the target's heart. Because of such, this spear is said to be impossible to dodge."

**Required Chunk 2** — `070_Scáthach_lore.md` (Lines 49–54):
> "『Gáe Bolg Alternative』 Rank: B NP Type: Anti-Personnel — Soaring Spear of Piercing Death. While it looks similar, it's different than the one possessed by Cú Chulainn. It is the same model as Gáe Bolg, but older, and instead of one spear there are two."

**Source Files:** `017_Cú_Chulainn_lore.md` × `070_Scáthach_lore.md`

---

## QA-20

**Question:** Altria Pendragon and Mordred share identical height and weight measurements. How does the lore account for this?

**Answer:**  
Both are listed at 154 cm and 42 kg. Mordred's profile explicitly states she "shares an identical physique to Altria." The explanation lies in Mordred's origin: she is a homunculus — an artificial human — created by the witch Morgan using Arthur's own lineage. By being modeled directly after Altria, Mordred naturally inherited the same physical frame. Mordred matured extremely quickly due to her nature as a homunculus, and her resemblance to Altria helped her rise to prominence as a knight almost immediately after entering Arthur's service.

**Required Chunk 1** — `002_Altria_Pendragon_lore.md` (Lines 30–31):
> "Height/Weight: 154cm, 42kg"

**Required Chunk 2** — `076_Mordred_lore.md` (Lines 28–34):
> "Height/Weight: 154cm, 42kg  
> ...  
> Shares an identical physique to Altria."

**Required Chunk 3** — `076_Mordred_lore.md` (Lines 38–45):
> "Mordred is an artificial creation, a type of homunculus, created by the witch Morgan... Being modeled after Arthur, Mordred gained prominence as a knight almost immediately."

**Source Files:** `002_Altria_Pendragon_lore.md` × `076_Mordred_lore.md`
