# Medical Technologist Curriculum Taxonomy v1

The machine-authoritative labels are in `taxonomy-v1.json`. This reference explains domain boundaries and gives compact examples. Do not infer a split from question order or an expected number of questions.

## Contents

- 臨床血液學與血庫學
- 臨床血清免疫學與臨床病毒學
- 臨床生理學與病理學
- 醫學分子檢驗學與臨床鏡檢學（包括寄生蟲學）
- Extension rules

## 臨床血液學與血庫學

### Domain decision

1. Blood group, compatibility, blood component, transfusion reaction, or maternal-fetal immunohematology → `MTH_HEM.BLOOD_BANK`.
2. Platelet biology, coagulation, bleeding, thrombosis, or anticoagulant monitoring → `MTH_HEM.HEMOSTASIS`.
3. White-cell maturation, reactive leukocytes, leukemia, lymphoma, myeloma, or marrow neoplasm → `MTH_HEM.WBC`.
4. Otherwise, red-cell production, indices, morphology, hemoglobin, anemia, or hemolysis → `MTH_HEM.RBC`.

### Chapters

- `MTH_HEM.RBC`: `HEMATOPOIESIS`, `INDICES_MORPH`, `ANEMIA`, `HEMOLYSIS`, `HEMOGLOBIN`, `TESTING`.
- `MTH_HEM.WBC`: `DEVELOPMENT`, `REACTIVE`, `ACUTE_LEUKEMIA`, `CHRONIC_MYELOID`, `LYMPHOID_PLASMA`, `TESTING`.
- `MTH_HEM.HEMOSTASIS`: `PLATELET_BIOLOGY`, `PLATELET_DISORDERS`, `COAGULATION`, `BLEEDING`, `THROMBOSIS_DIC`, `TESTING`.
- `MTH_HEM.BLOOD_BANK`: `ABO_RH`, `OTHER_GROUPS`, `ANTIBODY_TESTING`, `COMPATIBILITY`, `COMPONENTS`, `REACTIONS`, `HDFN`.

### Synthetic examples

- “Which index differentiates microcytic anemia?” → `MTH_HEM.RBC.ANEMIA`; evidence: `microcytic`, `MCV`.
- “Which marker supports acute promyelocytic leukemia?” → `MTH_HEM.WBC.ACUTE_LEUKEMIA`; evidence: `PML-RARA`.
- “An isolated prolonged aPTT is corrected by mixing.” → `MTH_HEM.HEMOSTASIS.BLEEDING`; secondary `MTH_HEM.HEMOSTASIS.TESTING` is allowed.
- “Which blood component is appropriate for fibrinogen replacement?” → `MTH_HEM.BLOOD_BANK.COMPONENTS`.
- Boundary: DAT used to investigate autoimmune hemolysis may be `RBC.HEMOLYSIS`; DAT used in an antibody workup or transfusion-reaction investigation is `BLOOD_BANK.ANTIBODY_TESTING` or `BLOOD_BANK.REACTIONS`.

## 臨床血清免疫學與臨床病毒學

### Domain decision

1. Named virus biology, pathogenesis, epidemiology, treatment, prevention, or virus-specific diagnostic strategy → `MTH_IMM_VIR.VIROLOGY`.
2. Immune mechanism, immune disease, antigen-antibody behavior, or general immunoassay principle → `MTH_IMM_VIR.IMMUNOLOGY`.
3. An immunoassay that detects a virus remains virology when virus-specific interpretation is required.

### Chapters

- `MTH_IMM_VIR.IMMUNOLOGY`: `INNATE_COMPLEMENT`, `ADAPTIVE`, `AG_AB`, `HYPERSENSITIVITY_AUTOIMMUNE`, `IMMUNODEF_TRANSPLANT`, `TUMOR_MONITORING`, `ASSAYS`.
- `MTH_IMM_VIR.VIROLOGY`: `BASICS`, `DNA`, `RNA`, `HEPATITIS`, `RETRO`, `ARBO_ZOONOTIC`, `DIAGNOSIS`, `TREAT_PREVENT`.

### Synthetic examples

- “Which complement component forms part of the membrane attack complex?” → `MTH_IMM_VIR.IMMUNOLOGY.INNATE_COMPLEMENT`.
- “What causes a false-negative result from antigen excess?” → `MTH_IMM_VIR.IMMUNOLOGY.AG_AB`.
- “How should HBsAg and anti-HBc IgM be interpreted?” → `MTH_IMM_VIR.VIROLOGY.HEPATITIS`; secondary `VIROLOGY.DIAGNOSIS` is allowed.
- “Which target is used by an HIV integrase inhibitor?” → `MTH_IMM_VIR.VIROLOGY.RETRO`; treatment can be secondary.
- Boundary: a question about ELISA sandwich format is `IMMUNOLOGY.ASSAYS`; a question about which HIV antigen an ELISA detects is `VIROLOGY.DIAGNOSIS`.

## 臨床生理學與病理學

### Domain decision

1. Organ function, physiologic value, waveform, functional-test operation, or functional interpretation → `MTH_PHY_PATH.PHYSIOLOGY`.
2. Cell or tissue morphology, injury mechanism, inflammation, neoplasia, histology, or pathologic diagnosis → `MTH_PHY_PATH.PATHOLOGY`.
3. A disease name does not decide the domain. Decide whether the tested task is function or structural/pathologic change.

### Chapters

- `MTH_PHY_PATH.PHYSIOLOGY`: `CARDIO`, `RESP`, `NEURO`, `NEUROMUSCULAR`, `RENAL`, `GI_LIVER`, `ENDOCRINE`.
- `MTH_PHY_PATH.PATHOLOGY`: `CELL_INJURY`, `INFLAMMATION_REPAIR`, `HEMODYNAMIC`, `IMMUNE`, `NEOPLASIA`, `SYSTEMIC`, `METHODS`.

### Synthetic examples

- “What change in FEV1/FVC suggests obstruction?” → `MTH_PHY_PATH.PHYSIOLOGY.RESP`.
- “Which EEG rhythm is expected during relaxed wakefulness?” → `MTH_PHY_PATH.PHYSIOLOGY.NEURO`.
- “Which pattern of necrosis occurs after cerebral infarction?” → `MTH_PHY_PATH.PATHOLOGY.CELL_INJURY`; `PATHOLOGY.SYSTEMIC` may be secondary.
- “Which step requires neutral buffered formalin?” → `MTH_PHY_PATH.PATHOLOGY.METHODS`.
- Boundary: myocardial-infarction ECG evolution is `PHYSIOLOGY.CARDIO`; myocardial coagulative necrosis is `PATHOLOGY.CELL_INJURY`.

## 醫學分子檢驗學與臨床鏡檢學（包括寄生蟲學）

Both ASCII and full-width parentheses are accepted subject aliases.

### Domain decision

1. Parasite morphology, life cycle, host, disease, epidemiology, or organism-focused diagnosis → `MTH_MOL_MIC_PAR.PARASITOLOGY`.
2. Nucleic-acid principle, primer/probe design, amplification, sequencing, variant interpretation, or molecular quality control → `MTH_MOL_MIC_PAR.MOLECULAR`.
3. Urine, CSF, serous fluid, synovial fluid, semen, non-parasitic stool, crystal, cell, or cast examination → `MTH_MOL_MIC_PAR.MICROSCOPY`.
4. For parasite PCR, classify by the tested knowledge: organism/clinical diagnosis → parasitology; PCR design or amplification principle → molecular.

### Chapters

- `MTH_MOL_MIC_PAR.MICROSCOPY`: `URINE_PHYSICAL_CHEM`, `URINE_SEDIMENT`, `CSF`, `BODY_FLUIDS`, `SEMEN`, `FECAL_OTHER`.
- `MTH_MOL_MIC_PAR.PARASITOLOGY`: `INTESTINAL_PROTOZOA`, `BLOOD_TISSUE_PROTOZOA`, `NEMATODES`, `CESTODES`, `TREMATODES`, `ARTHROPODS`, `DIAGNOSIS`.
- `MTH_MOL_MIC_PAR.MOLECULAR`: `NUCLEIC_ACID`, `PCR`, `HYBRIDIZATION`, `SEQUENCING`, `GENETICS`, `CLINICAL`, `QC`.

### Synthetic examples

- “Which cast is associated with glomerular bleeding?” → `MTH_MOL_MIC_PAR.MICROSCOPY.URINE_SEDIMENT`.
- “Which mosquito transmits a named parasite?” → the parasite chapter when the tested relationship is part of the parasite life cycle; use `ARTHROPODS` only when vector biology is primary.
- “Which stage of Plasmodium infects human erythrocytes?” → `MTH_MOL_MIC_PAR.PARASITOLOGY.BLOOD_TISSUE_PROTOZOA`.
- “How does primer-dimer affect real-time PCR?” → `MTH_MOL_MIC_PAR.MOLECULAR.PCR`.
- Boundary: detecting Giardia by PCR to identify the organism is `PARASITOLOGY.DIAGNOSIS`; choosing primer melting temperature is `MOLECULAR.PCR`.

## Extension rules

- Keep codes stable after results use them.
- Add a new chapter only when at least several questions share a coherent learning objective that existing chapters cannot represent.
- Add definitions, positive cues, exclusions, and boundary examples together.
- Prefer a secondary chapter over creating a hybrid domain.
- Create a new taxonomy version when splitting, merging, or changing the meaning of a chapter.
