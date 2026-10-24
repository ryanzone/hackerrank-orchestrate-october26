"""Observations for the image / PDF artifacts, from the stored multimodal transcription, plus the
data-catalog identity links (LINK_RESOURCE_IDENTITY rows in system_activity.csv)."""
import csv
import os
import re

from .evidence import Obs, keys_of
from .transcribed import ENTRIES

LINK_RE = re.compile(r'(?:linked|confirmed)\s+(\S+)\s+\(.*?\).*?(?:to|belongs only to)\s+(\S+@\S+?)\s+and\s+(user_\d+)')


def load(dataset_dir, files):
    out = []
    for e in ENTRIES:
        if e['file_id'] not in files:
            continue
        out.append(Obs(e['file_id'], e['observed_at'], e['resource_id'], e['kind'], e['status'],
                       keys_of(*e.get('identifiers', [])), 'transcribed', e['system'], {'visible': e['visible']}))
    for r in csv.DictReader(open(os.path.join(dataset_dir, 'system_activity.csv'), encoding='utf-8')):
        if r['event_type'] != 'LINK_RESOURCE_IDENTITY':
            continue
        m = LINK_RE.search(r['details'])
        if not m:
            continue
        rid, email, uid = m.groups()
        out.append(Obs(r['activity_id'], r['event_time'], r['resource_id'], 'link', 'LINK',
                       keys_of(email, uid), 'catalog-link', r['target_system']))
    return out
