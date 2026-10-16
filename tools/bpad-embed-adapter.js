/* Canopy MCT Generator — embed adapter for BasePlateAnchorDesigner.html.
   tools/sync-bpad.py appends this script to the designer (after all of its own
   scripts) before the designer is compressed into index.html. It runs inside the
   designer's iframe, after the designer's scripts have been parsed and before
   DOMContentLoaded, so it can replace the designer's global functions.

   - Never reads or writes the designer's saved work (bpad_* localStorage keys):
     the canopy page and the standalone designer can share one browser storage
     (same site), and an anchorage group must not overwrite a standalone project.
   - Every change the user makes in the designer is reported to the canopy page
     (window.parent.__bpadHost.changed), which stores it in the anchorage group.
   - window.BPAD_EMBED is the API the canopy page calls. */
(function(){
  var host=function(){ try{ return (window.parent!==window && window.parent.__bpadHost) || null; }catch(e){ return null; } };
  var quiet=0;
  var clone=function(o){ return JSON.parse(JSON.stringify(o)); };

  /* the designer's autosave hook: report the edit to the canopy page instead */
  window.autosave=function(){
    if(quiet) return;
    var h=host(); if(h && h.changed){ try{ h.changed(JSON.stringify(S)); }catch(e){} }
  };
  /* start from defaults; the canopy page loads the group's inputs */
  window.loadAutosave=function(){ return {ok:true, src:"canopy", name:""}; };

  var css=document.createElement("style");
  css.textContent=
    "#hdrBtns > :not(#btnPrint):not(#btnExportProfis){display:none !important}"+
    "#projMetaBtns,.allToolsLink,#restoreStatus{display:none !important}"+
    "#bpadEmbedNote{margin:4px 0 2px;padding:5px 8px;border:1px solid #c9b46a;background:#fbf6e3;color:#3d3420;font-size:9pt;border-radius:3px}";
  document.head.appendChild(css);

  window.BPAD_EMBED={
    commit:"__BPAD_COMMIT__",
    ready:false,
    defaults:function(){ return clone(defaultState()); },
    library:function(){ return {w:LIB.wshapes.map(function(s){return s.name;}), hss:LIB.hss.map(function(s){return {name:s.name,kind:s.kind,d:s.d,b:s.b};})}; },
    /* show a group's inputs in the designer */
    load:function(st){
      quiet++;
      try{ S=mergeState(clone(st)); if(typeof buildProjHeader==="function") buildProjHeader(); refreshAll(); }
      finally{ quiet--; }
    },
    get:function(){ return clone(S); },
    /* run every check for a group's inputs without touching what is on screen */
    evaluate:function(st){
      var sS=S, sC=CC, sV=VALIDATION;
      try{
        S=mergeState(clone(st)); S.ui=sS.ui;
        validateInputs();
        var bl=(VALIDATION.blockers||[]).map(function(b){ return (b.code?b.code+" — ":"")+b.msg; });
        var wr=(VALIDATION.warns||[]).map(function(w){ return typeof w==="string"?w:((w.code?w.code+" — ":"")+(w.msg||"")); });
        if(bl.length) return {ok:false, blockers:bl, warns:wr};
        CC=computeAll();
        var checks=allChecks().map(function(c){ return {label:c.label, ref:c.ref, dcr:c.dcr, combo:c.combo, na:!!c.na}; });
        var m=dcrMatrix();
        var rows=(m.rows||[]).map(function(r){ return {combo:r.combo, worst:r.worst, worstKey:r.worstKey}; });
        return {ok:true, checks:checks, rows:rows, modes:m.modes, warns:wr};
      }catch(e){ return {ok:false, err:String(e&&e.message||e)}; }
      finally{ S=sS; CC=sC; VALIDATION=sV; }
    },
    exportProfis:function(){ openProfisExport(); },
    print:function(){ openPrintSettings(); }
  };

  document.addEventListener("DOMContentLoaded",function(){
    /* the designer's init runs 60 ms after DOMContentLoaded */
    setTimeout(function(){
      var hdr=document.getElementById("hdr");
      if(hdr && !document.getElementById("bpadEmbedNote")){
        var n=document.createElement("div"); n.id="bpadEmbedNote";
        n.innerHTML="<b>Anchorage group from the canopy app.</b> The load combinations come from the columns assigned to this group and are replaced whenever the canopy analysis changes; edit every other input here. Changes are saved to the group (not to this designer's own projects).";
        hdr.appendChild(n);
      }
      window.BPAD_EMBED.ready=true;
      var h=host(); if(h && h.ready){ try{ h.ready(); }catch(e){} }
    },150);
  });
})();
