"""Has been moved into gfedb.py"""

# import os
# import sys
# import logging
# import ast
# import time
# from Bio import AlignIO
# from Bio.SeqFeature import SeqFeature
# from Bio.SeqRecord import SeqRecord
# from seqann.models.annotation import Annotation
# from Bio import SeqIO
# from pyard import ARD
# from seqann.gfe import GFE
# from csv import DictWriter
# from pathlib import Path
# from constants import *
# import hashlib

# logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#                     datefmt='%Y-%m-%d %H:%M:%S',
#                     level=logging.INFO)

# logging.debug(f'args: {sys.argv}')

# # Outputs memory of objects during execution to check for memory leaks
# if '-p' in sys.argv:
#     from pympler import tracker, muppy, summary

#     tr = tracker.SummaryTracker()

#     def memory_profiler(mode='all'):

#         # Print a summary of memory usage every n alleles
#         all_objects = muppy.get_objects()
#         obj_sum = summary.summarize(all_objects)

#         original_stdout = sys.stdout

#         if mode == 'all' or mode == 'agg':
#             with open("summary_agg.txt", "a+") as f:
#                 sys.stdout = f
#                 summary.print_(obj_sum)
#                 sys.stdout = original_stdout;

#         if mode == 'all' or mode == 'diff':
#             with open("summary_diff.txt", "a+") as f:
#                 sys.stdout = f
#                 tr.print_diff()
#                 sys.stdout = original_stdout;    

#         return


# def parse_dat(data_dir, dbversion):
    
#     try:
#         logging.info("Parsing DAT file...")
#         dat_file = ''.join([data_dir, 'hla.', dbversion, ".dat"])
        
#         return SeqIO.parse(dat_file, "imgt")
    
#     except Exception as err:
#         logging.error(f'Could not parse file: {dat_file}')
#         raise err


# def seq_hasher(seq, n=32):
#     """Takes a nucleotide or amino acid sequence and returns a reproducible
#     integer UUID. Used to create shorter unique IDs since Neo4j cannot index 
#     a full sequence. Can be also be used for any string."""

#     m = hashlib.md5()
#     m.update(seq)

#     return str(int(m.hexdigest(), 16))[:n]


# def parse_hla_alignments(dbversion, align_type="gen"):
    
#     if align_type in ["gen", "genomic"]:
#         align_type = "gen"
#     elif align_type in ["nuc", "nucleotide"]:
#         align_type = "nuc"
#     elif align_type in ["prot", "protein"]:
#         align_type = "prot"
#     else:
#         raise ValueError(f'Could not recognize align_type = "{align_type}"')
        
#     alignment = {l: {} for l in hla_loci}
    
#     for locus in hla_align:

#         msf = ''.join([data_dir, dbversion, "/", locus.split("-")[1], f"_{align_type}.msf"])

#         logging.info(f'Loading {"/".join(msf.split("/")[-3:])}')
#         align_data = AlignIO.read(open(msf), "msf")

#         seq = {"HLA-" + a.name: str(a.seq) for a in align_data}

#         del align_data

#         logging.info(f'{str(len(seq))} protein alignments loaded')
#         alignment.update({locus: seq})

#     return alignment


# def get_features(seqrecord):
#     j = 3 if len(seqrecord.features) > 3 else len(seqrecord.features)
#     fiveutr = [["five_prime_UTR", SeqRecord(seq=seqrecord.features[i].extract(seqrecord.seq), id="1")] for i in
#                range(0, j) if seqrecord.features[i].type != "source"
#                and seqrecord.features[i].type != "CDS" and isinstance(seqrecord.features[i], SeqFeature)
#                and not seqrecord.features[i].qualifiers]
#     feats = [[''.join([str(feat.type), "_", str(feat.qualifiers['number'][0])]), SeqRecord(seq=feat.extract(seqrecord.seq), id="1")]
#              for feat in seqrecord.features if feat.type != "source"
#              and feat.type != "CDS" and isinstance(feat, SeqFeature)
#              and 'number' in feat.qualifiers]

#     threeutr = []
#     if len(seqrecord.features) > 1:
#         threeutr = [["three_prime_UTR", SeqRecord(seq=seqrecord.features[i].extract(seqrecord.seq), id="1")] for i in
#                     range(len(seqrecord.features) - 1, len(seqrecord.features)) if
#                     seqrecord.features[i].type != "source"
#                     and seqrecord.features[i].type != "CDS" and isinstance(seqrecord.features[i], SeqFeature)
#                     and not seqrecord.features[i].qualifiers]

#     feat_list = fiveutr + feats + threeutr
#     annotation = {k[0]: k[1] for k in feat_list}

#     return annotation

    
# # Returns base pair and amino acid sequences from CDS data
# def get_cds(allele):

#     feat_types = [f.type for f in allele.features]
#     bp_seq = None
#     aa_seq = None
    
#     if "CDS" in feat_types:
#         cds_features = allele.features[feat_types.index("CDS")]
#         if 'translation' in cds_features.qualifiers:

#             if cds_features.location is None:
#                 logging.info(f"No CDS location for feature in allele: {allele.name}")
#             else:
#                 bp_seq = str(cds_features.extract(allele.seq))
#                 aa_seq = cds_features.qualifiers['translation'][0]
                
#     return bp_seq, aa_seq


# # Streams dictionaries as rows to a file
# def append_dict_as_row(dict_row, file_path):

#     try:
#         header = list(dict_row.keys())

#         # Check if file exists
#         csv_file = Path(file_path)
#         if not csv_file.is_file():

#             # Create the file and add the header
#             with open(file_path, 'a+', newline='') as write_obj:
#                 dict_writer = DictWriter(write_obj, fieldnames=header)
#                 dict_writer.writeheader()

#         # Do not add an else statement or the first line will be skipped
#         with open(file_path, 'a+', newline='') as write_obj:
#             dict_writer = DictWriter(write_obj, fieldnames=header)
#             dict_writer.writerow(dict_row)

#         return
#     except Exception as err:
#         logging.error(f'Could not add row')
#         raise err


# def get_groups(allele):
#     a_name = allele.description.split(",")[0].split("-")[1]
#     groups = [["HLA-" + ard.redux(a_name, grp), grp] if ard.redux(a_name, grp) != a_name else None for
#                 grp in ard_groups]

#     # expre_chars = ['N', 'Q', 'L', 'S']
#     # to_second = lambda a: ":".join(a.split(":")[0:2]) + \
#     #    list(a)[-1] if list(a)[-1] in expre_chars and \
#     #    len(a.split(":")) > 2 else ":".join(a.split(":")[0:2])
#     # seco = [[to_second(a_name), "2nd_FIELD"]]

#     return groups #list(filter(None, groups)) # + seco # --> why filter(None, ...) ?


# ### Refactor builed gfes
# def build_GFE(allele):
    
#     # Build and stream the GFE rows
#     try:
#         _seq = str(allele.seq)

#         row = {
#             "gfe_name": gfe_name,
#             "allele_id": allele.id,
#             "locus": locus,
#             "hla_name": hla_name,
#             #"a_name": hla_name.split("-")[1],
#             "seq_id": seq_hasher(_seq.encode('utf-8')),
#             "sequence": _seq,
#             "length": len(_seq),
#             "imgt_release": imgt_release
#         }

#         return row
               
#     except Exception as err:
#         logging.error(f'Failed to write GFE data for allele ID {allele.id}')
#         raise err 


# def build_feature(feature):
    
#     try:

#         feature["gfe_name"] = gfe_name
#         feature["term"] = feature["term"].upper()
#         feature["allele_id"] = allele.id 
#         feature["hla_name"] = hla_name
#         feature["imgt_release"] = imgt_release

#         # Avoid null values in CSV for Neo4j import
#         feature["hash_code"] = "none" if not feature["hash_code"] else feature["hash_code"]

#         return feature

#     except Exception as err:
#         logging.error(f'Failed to write feature for allele {allele.id}')
#         logging.error(err)


# def build_alignment(allele, alignments, align_type="genomic"):

#     if align_type in ["gen", "genomic"]:
#         align_type = "genomic"
#         label = "GEN_ALIGN"
#     elif align_type in ["nuc", "nucleotide"]:
#         align_type = "nucleotide"
#         label = "NUC_ALIGN"
#     elif align_type in ["prot", "protein"]:
#         align_type = "protein"
#         label = "PROT_ALIGN"
#     else:
#         raise ValueError(f'Could not recognize align_type = "{align_type}"')

#     if allele.description.split(",")[0] in alignments[align_type][locus]:
      
#         try:
            
#             alignment = alignments[align_type][locus][allele.description.split(",")[0]]

#             row = {
#                 "label": label,
#                 "seq_id": seq_hasher(alignment.encode('utf-8')),
#                 "gfe_name": gfe_name,
#                 "hla_name": hla_name,
#                 #"a_name": hla_name.split("-")[1],
#                 "length": len(alignment),
#                 "rank": "0", # TO DO: confirm how this value is derived
#                 #"bp_sequence": alignment if align_type in ["genomic", "nucleotide"] else "",
#                 #"aa_sequence": "",
#                 "imgt_release": imgt_release # 3.24.0 instead of 3240
#             }
            
#             if align_type == "protein":
#                 row.update({
#                 "bp_sequence": "",
#                 "aa_sequence": alignment,
#                 })
#             else:
#                 row.update({
#                 "bp_sequence": alignment,
#                 "aa_sequence": "",
#                 })
                
#             return row
    
#         except Exception as err:
#             logging.error(f'Failed to write {align_type} alignment for allele {allele.id}')
#             raise err
    
#     else:
#         logging.info(f'No {align_type} alignments found')
#         return


# def build_group(group, allele):
#     # Build and stream the ARD group rows
#     try:
#         row = {
#             "gfe_name": gfe_name,
#             "allele_id": allele.id,
#             "hla_name": hla_name,
#             #"a_name": hla_name.split("-")[1],
#             "ard_id": group[0],
#             "ard_name": group[1],
#             "locus": locus,
#             "imgt_release": imgt_release
#         }
        
#         return row

#     except Exception as err:
#         logging.error(f'Failed to write groups for allele {allele.id}')
#         raise err


# def build_cds(allele):
#     # Build and stream the CDS rows
#     try:
#         # Build CDS dict for CSV export, foreign key: allele_id, hla_name
#         bp_seq, aa_seq = get_cds(allele)

#         row = {
#             "gfe_name": gfe_name,
#             # "gfe_sequence": str(allele.seq),
#             # "allele_id": allele.id,
#             # "hla_name": hla_name,
#             "bp_seq_id": seq_hasher(bp_seq.encode('utf-8')),
#             "bp_sequence": bp_seq,
#             "aa_seq_id": seq_hasher(aa_seq.encode('utf-8')),
#             "aa_sequence": aa_seq,
#             # "imgt_release": imgt_release
#         }

#         return row

#     except Exception as err:
#         logging.error(f'Failed to write CDS data for allele {allele.id}')
#         raise err


# def gfe_from_allele(allele, gfe_maker):

#     complete_annotation = get_features(allele)

#     ann = Annotation(annotation=complete_annotation,
#             method='match',
#             complete_annotation=True)

#     # This process takes a long time
#     logging.info(f"Getting GFE data for allele {allele.id}...")
#     features, gfe = gfe_maker.get_gfe(ann, locus)
        
#     return { 
#         "name": gfe,
#         "features": features
#     }


# def process_allele(allele, alignments, csv_path=None):
    
#     csv_path = csv_path[:-1] if csv_path[-1] == "/" else path

#     # gfe_sequences.RELEASE.csv
#     file_name = f'{csv_path}/gfe_sequences.{dbversion}.csv'
#     gfe_row = build_GFE(allele)
#     append_dict_as_row(gfe_row, file_name)

#     # all_features.RELEASE.csv
    
#     # features preprocessing steps
#     # 1) Convert seqann type to python dict using literal_eval
#     # 2) add GFE foreign keys: allele_id, hla_name
#     # 3) calculate columns: length             

#     # Append allele id's
#     # Note: Some alleles may have the same feature, but it may not be the same rank, 
#     # so a feature should be identified with its allele by allele_id or HLA name
    
#     # features contains list of seqann objects, converts to dict, destructive step
#     features = \
#         [ast.literal_eval(str(feature) \
#             .replace('\'', '"') \
#             .replace('\n', '')) \
#             for feature in gfe_features]  

#     file_name = f'{csv_path}/all_features.{dbversion}.csv'

#     for feature in features:
#         feature_row = build_feature(feature)
#         append_dict_as_row(feature_row, file_name)

#     # all_alignments.RELEASE.csv
#     if alignments:
#         file_name = f'{csv_path}/all_alignments.{dbversion}.csv'
        
#         for align_type in ["genomic", "nucleotide", "protein"]:
#             append_dict_as_row(
#                 build_alignment(allele=allele, 
#                                 alignments=alignments_dict,
#                                 align_type=align_type), 
#                 file_name)
            
#     # all_groups.RELEASE.csv
#     groups = get_groups(allele)

#     file_name = f'{csv_path}/all_groups.{dbversion}.csv'

#     for group in groups:
#         group_row = build_group(group, allele)
#         append_dict_as_row(group_row, file_name)

#     # all_cds.RELEASE.csv
#     file_name = f'{csv_path}/all_cds.{dbversion}.csv'
#     append_dict_as_row(build_cds(allele), file_name)
        
#     return

