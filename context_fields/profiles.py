"""M3a only: attach readouts to existing, evidence-backed topic hypotheses.

No category generator, aliases, model invocation, mass update or cross-run release.
"""
import copy
import hashlib


class TopicProfiles:
    def __init__(self, ledger):
        self.ledger=ledger; self.profiles={}; self.turn=0; self.revision=0

    def sync(self, snapshot):
        for row in snapshot['spaces']['conversation']['hypotheses']:
            if row['facet']!='conversation.topic':continue
            sources=[s for sign in row['sources'].values() for s in sign]
            if not sources:continue
            # Existing TextContext quote is required; the label alone is insufficient.
            valid=[]
            for source in sources:
                obs=self.ledger.records[source['observation_id']]
                asr=self.ledger.records.get(obs.get('asr_id'),{})
                if obs.get('quote') and obs['quote'] in asr.get('text','') and obs.get('asr_id') in self.ledger.active:
                    valid.append(obs)
            if not valid:continue
            pid='topic-'+hashlib.sha256(row['id'].encode()).hexdigest()[:16]
            if pid in self.profiles:continue
            self.revision+=1
            profile={'id':pid,'revision':1,'registry_revision':self.revision,'profile_bundle_id':'existing-evidence/'+row['id'],
                'evidence_profile':'existing-relation-relevance-routing-v1','dominance_template':'independent-score-assessability-v1',
                'rubric_version':'ja-topic-score-v1','candidate_set_version':1,'epoch':snapshot['epoch'],
                'space':'conversation','facet':'conversation.topic','subject':row['subject'],'scope':row['scope'],
                'reference_mode':row['reference_mode'],'category_ids':[row['id']], 'category_label':row['value'],
                'definition':f"発話原文が既存の話題「{row['value']}」について述べている程度。",
                'exclusions':['会話様式の判定ではない','原文にない内容を補わない','ラベルの存在だけでは支持にしない'],
                'required_input':'evaluated_textcontext_with_exact_asr_quote',
                'adopted_cursor':len(self.ledger.events)+1,'design_refs':sorted({o['id'] for o in valid}),
                'status':'registered_not_scheduled','runtime_verified':False,'origin':'existing_category_connection'}
            self.profiles[pid]=profile;self.ledger.event('profile_registered',profile)

    def public(self):return copy.deepcopy(list(self.profiles.values()))

    def select(self, eligible, occupied=(),capacity=2):
        for p in self.profiles.values():p['status']='registered_not_scheduled'
        busy=set(occupied)&self.profiles.keys()
        for k in busy:self.profiles[k]['status']='scheduled'
        keys=sorted(k for k in eligible if k in self.profiles and k not in busy)
        if not keys:return []
        capacity=len(keys) if capacity is None else max(0,capacity-len(busy))
        start=self.turn%len(keys);self.turn+=capacity
        selected=(keys[start:]+keys[:start])[:capacity]
        for k in selected:self.profiles[k]['status']='scheduled'
        return selected
