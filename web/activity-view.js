'use strict';
// Shared incremental renderer. It takes only arrived notifications, never a future array.
(function(root){
  const reason={unsupported_schema:'表示項目に未対応',not_recorded:'記録なし',not_evaluated:'未評価',readout_not_arrived:'公開記録待ち',digital_silence:'デジタル無音',unvoiced_or_unavailable:'取得不可',comparison_or_scale_not_recorded:'比較情報なし',discontinuity:'入力が不連続',cut_detected:'場面転換検出時は未計測',score_expectation:'C0棄却'};
  function stamp(t){if(!Number.isFinite(t)||t<0)return '—';return `${Math.floor(t/60).toString().padStart(2,'0')}:${(t%60).toFixed(3).padStart(6,'0')}`;}
  function value(f){
    if(f.value===null||f.value===undefined)return reason[f.missing_reason]||'取得不可';
    if(f.display_value)return f.display_value;
    if(f.type==='boolean')return f.value?'検出あり':'検出なし';
    if(f.type==='number'){
      const n=f.value;if(typeof n!=='number'||!Number.isFinite(n))return '取得不可';
      const digits=f.unit==='dBFS'||f.unit==='Hz'?1:2,threshold=10**(-digits);
      const s=n!==0&&Math.abs(n)<threshold?(n<0?'−':'')+'<'+threshold:n.toFixed(digits);
      return s+(f.unit?' '+f.unit:'');
    }
    return typeof f.value==='string'?f.value:'表示項目に未対応';
  }
  function fields(parent,items,onSelect){
    parent.replaceChildren();
    for(const f of items){const wrap=document.createElement(onSelect?'button':'div');wrap.className='h11-field';
      const label=document.createElement('dt'),content=document.createElement('dd');label.textContent=f.label;content.textContent=value(f);wrap.append(label,content);
      if(onSelect){wrap.type='button';wrap.onclick=()=>onSelect(f);}parent.append(wrap);}
    if(!items.length){const n=document.createElement('p');n.textContent='結果待ち';parent.append(n);}
  }
  class ActivityView{
    constructor(container,onSelect){this.container=container;this.onSelect=onSelect;this.rows=new Map();this.nodes=new Map();this.run=null;this.generation=0;this.frozen=null;}
    reset(run,generation){this.run=run;this.generation=generation;this.rows.clear();this.nodes.clear();this.container.replaceChildren();this.frozen=null;}
    freeze(rowId){this.frozen=rowId;}
    accept(message){
      if(message.run_id!==this.run||String(message.generation)!==String(this.generation))return false;
      if(message.type==='thumbnail')return this.thumbnail(message);
      const row=message.row,old=this.rows.get(row?.row_id);
      if(!row||row.run_id!==this.run||!Number.isInteger(row.revision)||old&&row.epoch!==old.epoch)return false;
      if(old&&row.revision<old.revision)return false;
      if(old&&row.revision===old.revision){
        if((row.state_cursor||0)<=(old.state_cursor||0))return false;
        // Evaluation/TTL state is independent of the source result version. Keep
        // its DOM and loaded image; do not re-rank the original result.
        old.state_cursor=row.state_cursor;old.evaluation_state=row.evaluation_state;old.evaluation=row.evaluation;
        this.nodes.get(row.row_id).producer.textContent=old.producer+(old.evaluation_state?' · '+old.evaluation_state:'');return true;
      }
      if(this.frozen===row.row_id)return false;
      if(message.type==='invalidate'){this.rows.set(row.row_id,{...row,processing_state:'invalidated'});this.paint(row.row_id);return true;}
      this.rows.set(row.row_id,structuredClone(row));this.paint(row.row_id);return true;
    }
    paint(id){
      const row=this.rows.get(id);let node=this.nodes.get(id);
      if(!node){
        const button=document.createElement('button');button.type='button';button.className='h1-row';button.dataset.row=id;
        const media=document.createElement('div');media.className='h11-media';const images=document.createElement('div');images.className='h11-images';
        const caption=document.createElement('span');caption.className='h11-source-time';media.append(images,caption);
        const body=document.createElement('div');body.className='h11-body';const head=document.createElement('div');head.className='h11-row-head';
        const producer=document.createElement('strong'),time=document.createElement('time');head.append(producer,time);
        const list=document.createElement('dl');list.className='h11-fields';body.append(head,list);button.append(media,body);
        button.onclick=()=>this.onSelect(this.rows.get(id));node={button,media,images,caption,producer,time,list};this.nodes.set(id,node);this.container.append(button);
      }
      node.producer.textContent=row.producer+(row.evaluation_state?' · '+row.evaluation_state:'');node.time.textContent=row.processing_state==='running'?'処理中…':(row.processing_state==='error'?'失敗 · ':'')+'結果 +'+stamp(row.available_at);
      fields(node.list,row.processing_state==='running'?[]:row.result_fields||[]);
      const refs=(row.source_refs||[]).filter(r=>r.modality==='video');node.images.replaceChildren();
      if(refs.length){for(const ref of refs.slice(0,2)){const p=document.createElement('span');p.className='h11-thumb-placeholder';p.textContent='画像準備中';p.dataset.ref=ref.ref_id;node.images.append(p);}node.caption.textContent='元画像 '+refs.slice(0,2).map(r=>stamp(r.media_s)).join(' / ');}
      else {const p=document.createElement('span');p.className='h11-thumb-placeholder';p.textContent=row.thumbnail_state==='audio'?'▶ 音声':'画像なし';node.images.append(p);
        const audio=(row.source_refs||[]).filter(r=>r.modality==='audio');node.caption.textContent=audio.length?'音声 '+stamp(Math.min(...audio.map(r=>r.interval[0])))+'–'+stamp(Math.max(...audio.map(r=>r.interval[1]))):'';}
      if(row.source_count>(row.source_refs||[]).length)node.caption.textContent+=' · 他'+(row.source_count-row.source_refs.length)+'件';
      if(row.field_count>(row.result_fields||[]).length){const more=document.createElement('span');more.className='h11-more-fields';more.textContent='ほか'+(row.field_count-row.result_fields.length)+'項目 · 選択して全文';node.list.append(more);}
    }
    thumbnail(message){
      const row=this.rows.get(message.row_id),node=this.nodes.get(message.row_id);
      if(!row||row.revision!==message.revision||row.epoch!==message.epoch||!node||!(row.source_refs||[]).some(r=>r.ref_id===message.ref_id))return false;
      const target=[...node.images.children].find(n=>n.dataset.ref===message.ref_id);if(!target)return false;
      (row.thumbnail_results||={})[message.ref_id]={state:message.state,reason:message.reason||null,cache_key:message.cache_key||null,provenance:message.provenance||null};
      target.replaceChildren();if(message.state==='ready'&&message.jpeg){const img=document.createElement('img');img.src='data:image/jpeg;base64,'+message.jpeg;img.alt='この結果の参照画像';target.textContent='';target.append(img);}else target.textContent=message.state==='pending'?'画像準備中':'画像取得不可';
      return true;
    }
    retain(ids){for(const [id,node] of this.nodes)if(!ids.has(id)){node.button.remove();this.nodes.delete(id);this.rows.delete(id);}}
    order(ids){for(let i=0;i<ids.length;i++){const node=this.nodes.get(ids[i])?.button;if(node&&this.container.children[i]!==node)this.container.insertBefore(node,this.container.children[i]||null);}}
  }
  // Each pane owns its anchor. Native anchoring is disabled in CSS. Programmatic
  // scroll events are ignored only when they match our last correction exactly.
  class ScrollAnchor{
    constructor(view,onChange=()=>{}){
      this.view=view;this.container=view.container;this.onChange=onChange;this.anchor=null;this.expected=null;
      this.container.onscroll=()=>{if(this.expected!==null&&Math.abs(this.container.scrollTop-this.expected)<1){this.expected=null;return;}this.capture();this.onChange(!!this.anchor,true);};
      if(typeof ResizeObserver!=='undefined'){this.resize=new ResizeObserver(()=>this.restore());this.resize.observe(this.container);}
    }
    capture(){
      const box=this.container.getBoundingClientRect?.();
      if(this.container.scrollTop<=2||!box){this.anchor=null;return null;}
      const top=box.top+(this.container.clientTop||0);
      const node=[...this.container.children].find(n=>n.dataset.row&&n.getBoundingClientRect().bottom>top);
      this.anchor=node?{id:node.dataset.row,offset:node.getBoundingClientRect().top-top}:this.anchor;
      return this.anchor;
    }
    restore(saved=this.anchor){
      this.anchor=saved;
      if(saved){const node=this.view.nodes.get(saved.id)?.button,box=this.container.getBoundingClientRect?.();
        if(node&&box)this.container.scrollTop+=node.getBoundingClientRect().top-box.top-(this.container.clientTop||0)-saved.offset;
      }else this.container.scrollTop=0;
      this.expected=this.container.scrollTop;this.onChange(!!this.anchor);
    }
    latest(){this.anchor=null;this.restore(null);}
  }
  class EvaluationLists{
    constructor(logs,evaluations,onSelect,onScroll=()=>{}){
      this.logs=new ActivityView(logs,onSelect);this.evaluations=new ActivityView(evaluations,onSelect);
      this.anchors={logs:new ScrollAnchor(this.logs,(x,manual)=>onScroll('logs',x,manual)),evaluations:new ScrollAnchor(this.evaluations,(x,manual)=>onScroll('evaluations',x,manual))};
      this.completions=new Map();this.selected=null;
    }
    reset(run,generation){this.run=run;this.generation=generation;this.selected=null;this.completions.clear();for(const p of ['logs','evaluations']){this[p].reset(run,generation);this.anchors[p].latest();}}
    freeze(id){this.selected=id;/* H0 pin is independent; status may update without changing the fixed form. */}
    protected(){return [...new Set([this.selected,this.anchors.logs.capture()?.id].filter(Boolean))];}
    latest(pane){this.anchors[pane].latest();this.reconcile();}
    accept(message){
      if(message.run_id!==this.run||String(message.generation)!==String(this.generation))return false;
      if(message.type==='thumbnail'){
        const pane=this.evaluations.rows.has(message.row_id)?'evaluations':'logs',anchor=this.anchors[pane].capture();
        const ok=this[pane].accept(message);this.anchors[pane].restore(anchor);return ok;
      }
      return this.transaction({...message,rows:message.rows||[message.row].filter(Boolean)});
    }
    transaction(message){
      if(message.run_id!==this.run||String(message.generation)!==String(this.generation))return false;
      const saved={logs:this.anchors.logs.capture(),evaluations:this.anchors.evaluations.capture()};
      const protectedIds=new Set([this.selected,saved.logs?.id,saved.evaluations?.id].filter(Boolean));
      for(const row of message.rows||[]){
        const pane=row.pane==='evaluations'?'evaluations':'logs';this[pane].accept({type:'result',run_id:this.run,generation:this.generation,row});
      }
      // A source-row completion is supplied by the read-only projector, with exact
      // source result sequence and all direct evaluation references. It is not inferred
      // from a card's words, time, roots, or membership in a request.
      for(const c of message.completions||[]){const old=this.completions.get(c.row_id);if(!old||c.result_seq>=old.result_seq)this.completions.set(c.row_id,structuredClone(c));}
      if(message.snapshot){for(const p of ['logs','evaluations'])this[p].retain(new Set([...(message.rows||[]).filter(r=>r.pane===p).map(r=>r.row_id),...protectedIds]));}
      this.reconcile(protectedIds);
      for(const p of ['logs','evaluations'])this.anchors[p].restore(saved[p]);
      return true;
    }
    reconcile(protectedIds=new Set(this.protected())){
      for(const [id,c] of this.completions){
        if(!c.references_seen&&c.evaluation_ids?.length&&c.evaluation_ids.every(e=>this.evaluations.rows.has(e))){
          c.references_seen=true;c.certified_at=Math.max(...c.evaluation_ids.map(e=>this.evaluations.rows.get(e).result_seq));
        }
        const row=this.logs.rows.get(id);
        if(!row||row.epoch!==c.epoch||row.result_seq!==c.result_seq||!c.references_seen)continue;
        if(row.state_cursor>c.certified_at&&row.evaluation?.complete===false)continue; // later target-set state wins
        row.evaluation_state='評価済み';row.evaluation={...row.evaluation,complete:true};this.logs.paint(id);
      }
      const ids=[...this.logs.rows.values()].filter(r=>!r.evaluation?.complete||protectedIds.has(r.row_id)).map(r=>r.row_id);
      this.logs.retain(new Set(ids));
      for(const p of ['logs','evaluations']){
        const rows=[...this[p].rows.values()].sort((a,b)=>(b.result_seq??b.event_seq)-(a.result_seq??a.event_seq)||b.row_id.localeCompare(a.row_id));
        const anchor=this.anchors[p].anchor?.id,rank=rows.findIndex(r=>r.row_id===anchor),start=Math.max(0,rank-5);
        const bounded=rows.length<=60?rows:rows.slice(start,start+60);
        const chosen=this[p].rows.get(this.selected);
        if(chosen&&!bounded.some(r=>r.row_id===chosen.row_id)){if(bounded.length===60)bounded.pop();bounded.push(chosen);}
        const kept=new Set(bounded.map(r=>r.row_id)),ids=rows.filter(r=>kept.has(r.row_id)).map(r=>r.row_id);this[p].retain(kept);this[p].order(ids);
      }
    }
  }
  root.H11={ActivityView,ScrollAnchor,EvaluationLists,fields,value,stamp};
})(typeof window==='undefined'?module.exports:window);
