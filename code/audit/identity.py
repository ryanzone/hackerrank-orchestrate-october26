"""Identity resolution from user_identities.csv + user_audit_log.csv."""
import csv
import os
from collections import defaultdict

from .evidence import norm_key


class Identity:
    def __init__(self, user_id):
        self.user_id = user_id
        self.keys = {'u:' + user_id.lower()}
        self.names = set()

    def add(self, v):
        k = norm_key(v)
        if k:
            self.keys.add(k)


class IdentityIndex:
    def __init__(self, dataset_dir):
        self.users = {}
        self.by_key = defaultdict(set)
        for r in csv.DictReader(open(os.path.join(dataset_dir, 'user_identities.csv'), encoding='utf-8')):
            u = self.users.setdefault(r['user_id'], Identity(r['user_id']))
            for f in ('email', 'phone', 'email_hash'):
                u.add(r[f])
            u.names.add(r['name'])
        for r in csv.DictReader(open(os.path.join(dataset_dir, 'user_audit_log.csv'), encoding='utf-8')):
            u = self.users.setdefault(r['user_id'], Identity(r['user_id']))
            u.add(r['old_value'])
            u.add(r['new_value'])
        for uid, u in self.users.items():
            for k in u.keys:
                if k.startswith(('e:', 'h:')):
                    self.by_key[k].add(uid)

    def resolve(self, email):
        k = norm_key(email)
        return sorted(self.by_key.get(k, ()))
