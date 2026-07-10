# Chimera Cortex — Retrieval Shortfalls Analysis (Run ID 19)

Despite the implementation of **dense-sparse hybrid retrieval (dense + BM25 text match with RRF fusion)** and **query decomposition**, there are still targeted retrieval shortfalls in Run ID 19. 

Below is the deep-dive diagnostics of the exact architectural limits that caused these remaining failures.

---

## 1. The Four Core Retrieval Gaps in Run 19

### Shortfall A: Needle-in-a-Haystack Cross-References (QA-17)
* **Question**: *How does Karna view Gilgamesh, and how does that perspective reflect on Karna's own circumstances?*
* **The Gap**: The critical sentence explaining Karna's view of Gilgamesh (his admiration and jealousy) is located in `305_Amor_(Caren)_lore.md` (a document primarily about **Amor/Caren**).
* **Why Hybrid + Decomposition Failed**: 
  1. Query decomposition generated focused sub-queries centered on **"Karna"** and **"Gilgamesh"**.
  2. Because the target chunk resides inside **Caren's** profile, the overall semantic representation of that chunk is dominated by Caren-related terminology.
  3. Consequently, the dedicated profile documents `085_Karna_lore.md` and `145_Gilgamesh_lore.md` had significantly higher cosine similarity and BM25 match scores, completely drowning out the Caren document.
* **Verdict**: Hybrid search fails when the golden detail for Entity A and B resides within a document dominated by Entity C.

---

### Shortfall B: Stat Block vs. Bio Chunking Separation (QA-20)
* **Question**: *Altria Pendragon and Mordred share identical height and weight measurements. How does the lore account for this?*
* **The Gap**: The factual connections are spread across physically separated segments of the source files.
* **Why Hybrid + Decomposition Failed**:
  1. The specific height/weight measurements (154cm, 42kg) reside in the **top-level metadata stat blocks** of `002_Altria_Pendragon_lore.md` and `076_Mordred_lore.md`.
  2. The explanation (Mordred is a homunculus modeled directly after Altria to inherit her physical frame) resides in the **narrative text paragraphs** in the middle of Mordred's file.
  3. The RAG chunker split the stat tables and the narrative paragraphs into **separate chunks**. 
  4. The model retrieved the stat chunks and the bio chunks, but because they lacked a cohesive "bridge sentence" explicitly linking the measurements to the cloning process in a single retrieved passage, the LLM over-triggered its strict safety prompt, concluding that the documents lacked "sufficient information" to connect them.
* **Verdict**: Standard chunking breaks tabular metadata away from descriptive text, destroying logical bridges.

---

### Shortfall C: Semantic Merging Slicing Cut-Off (QA-11)
* **Question**: *What abilities did Scáthach teach Cú Chulainn, and how does Cú Chulainn make use of those teachings differently when summoned as a Lancer versus a Caster?*
* **The Gap**: The retriever successfully pulled Cú Chulainn's Caster-specific spells (Ansuz rune, fire/heat), but failed to include his Lancer-specific limits (sealing his runes because he finds them a nuisance).
* **Why Hybrid + Decomposition Failed**:
  1. Query decomposition successfully generated distinct sub-queries for Caster and Lancer.
  2. However, the API router merging logic (`cortex/api/chat.py`) merges all sub-query context lists, deduplicates them, sorts them by distance, and slices the result to the **top 10** (`contexts = contexts[:10]`).
  3. Because Caster and general Cú Chulainn chunks returned highly dense similarities, they dominated the top rankings. The highly specific Lancer chunk detailing him sealing his runes was squeezed out of the final 10 contexts.
* **Verdict**: Fixed global context slicing (`top_k = 10`) after multi-query merging causes asymmetric retrieval loss.

---

### Shortfall D: Lineage & Factional Relational Limits (QA-15)
* **Question**: *What is the relationship between Karna and Arjuna, and why were they destined to become enemies?*
* **The Gap**: The model generated circular family tree logic (Kunti adopting children abandoned by Kunti) and inverted Duryodhana's alliance.
* **Why Hybrid + Decomposition Failed**:
  1. The contexts for Karna and Arjuna were both successfully retrieved.
  2. However, RAG retrieval does not build a **structured knowledge graph** of relations. The model received two separate narrative descriptions of two different characters' lineages.
  3. Without structured entity relations, the LLM struggled to stitch the disparate maternal references (Kunti abandoning Karna vs. Kunti raising Arjuna) into a coherent lineage, resulting in synthesis circularity.
* **Verdict**: High retrieval relevance does not guarantee relational reasoning; raw text retrieval lacks semantic relational structuring.

---

## 2. Actionable Engineering Fixes

To solve these exact four shortfalls, we should implement:

1. **Entity-Balanced Retrieval Slicing**: Instead of a global post-merge slice (`top 10`), allocate a guaranteed quota per decomposed sub-query (e.g., take the top 3 chunks *specifically* from the Lancer sub-query and top 3 from the Caster sub-query) to prevent one topic from dominating.
2. **Parent Metadata & Tabular Attribute Prepends**: Prepend document-level structured properties or global tabular metadata (such as character/entity stats, names, or classification metadata) to *every* narrative text chunk generated from that document. This ensures that quantitative tabular facts remain contextually bound to qualitative explanations even after chunk segmentation.
3. **Multi-Entity Co-occurrence Query Expansion**: When a query references multiple distinct primary entities, programmatically expand the sparse keyword search query with boolean co-occurrence constraints (e.g., `EntityA AND EntityB`) to target specific paragraphs where both topics intersect. This bypasses global page-level vector biases that tend to favor individual entity profile documents.
