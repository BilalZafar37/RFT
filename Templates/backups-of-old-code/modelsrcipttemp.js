<!-- MODEL SCRIPT FOR ADD ITEMS -->
{/* <script> */}
                  // pull your backend list of {ShipmentPOLineID, PONumber, Article, Qty} into JS
                  const reportData = {{ report_data_json | tojson | safe }};
                  let containerItems = {};
                  let modalContainerId = null;
                
                  function getAdjustedAvailableQty(polineId, currentRow=null) {
                    const original = $(`option[value="${polineId}"]`).data('available') || 0;
                    let usedElsewhere = 0;
                    for (let cid in containerItems) {
                      if (cid !== modalContainerId) {
                        containerItems[cid].forEach(it => {
                          if (it.poline_id === polineId) usedElsewhere += it.selected_qty;
                        });
                      }
                    }
                    let usedInModal = 0;
                    $('#modalItemsTable tbody tr').each(function() {
                      const pid = +$(this).find('.modal-article').val();
                      const qty = +$(this).find('.modal-selected-qty').val()||0;
                      if (pid===polineId && this!==currentRow) usedInModal += qty;
                    });
                    return Math.max(0, original - usedElsewhere - usedInModal);
                  }
                
                  // open modal
                  $(document).on('click','.add-items-btn',function(){
                    modalContainerId = $(this).data('container-id');
                    $('#modal-container-id').val(modalContainerId);
                    $('#modalItemsTable tbody').empty();
                    (containerItems[modalContainerId]||[{poline_id:null,available_qty:0,selected_qty:0}])
                      .forEach(it=> addModalItemRow(it.poline_id, it.selected_qty));
                    $('#containerItemsModal').modal('show');
                  });
                
                  // add blank row
                  $('#add-modal-row').click(function(){
                    let ok=true;
                    $('#modalItemsTable tbody tr').each(function(){
                      if ((+$(this).find('.modal-selected-qty').val()||0)===0) ok=false;
                    });
                    if(!ok){ return alert("Fill all quantities first."); }
                    addModalItemRow(null,0);
                  });
                
                  function addModalItemRow(selPoline, selQty){
                    const row = $('<tr>');
                    const sel = $('<select class="modal-article form-control">');
                    // figure out which POLineIDs are already picked here
                    const picked = new Set();
                    $('#modalItemsTable tbody tr').each(function(){
                      const v = +$(this).find('.modal-article').val();
                      if(v) picked.add(v);
                    });
                    // populate
                    reportData.forEach(r=>{
                      const opt = $('<option>')
                        .val(r.ShipmentPOLineID)
                        .text(`${r.PONumber} - ${r.Article}`)
                        .attr('data-available', r.Qty)
                        .attr('data-article',   r.Article)
                        .attr('data-po',        r.PONumber);
                      if (picked.has(r.ShipmentPOLineID) && r.ShipmentPOLineID!==selPoline) {
                        opt.prop('disabled',true).text(opt.text()+' (Already)');
                      }
                      sel.append(opt);
                    });
                    const availTd = $('<td class="availQty">');
                    const qtyIn   = $(`<input type="number" class="modal-selected-qty form-control" min="0" value="${selQty||0}">`);
                    const qtyTd   = $('<td>').append(qtyIn);
                    const rmBtn   = $('<button type="button" class="remove-modal-row btn btn-sm btn-danger">Remove</button>');
                    const actTd   = $('<td>').append(rmBtn);
                
                    row.append($('<td>').append(sel), availTd, qtyTd, actTd);
                    $('#modalItemsTable tbody').append(row);
                
                    function refresh() {
                      const pid = +sel.val();
                      const max = getAdjustedAvailableQty(pid, row[0]);
                      availTd.text(max);
                      qtyIn.attr('max',max).prop('readonly', max===0);
                    }
                    // on change
                    sel.on('change',refresh);
                    qtyIn.on('input',function(){
                      const m = +$(this).attr('max')||0, v=+this.value||0;
                      if(v>m) this.value=m;
                    });
                    // remove
                    rmBtn.click(()=>{
                      row.remove();
                      saveToContainerItems();
                      refreshAll(); 
                    });
                
                    // initialize
                    if(selPoline) sel.val(selPoline);
                    else sel.find('option:not(:disabled)').first().prop('selected',true);
                    sel.trigger('change');
                  }
                
                  // write current modal rows back into containerItems
                  function saveToContainerItems(){
                    const out=[];
                    $('#modalItemsTable tbody tr').each(function(){
                      const opt  = $(this).find('.modal-article option:selected');
                      const pid  = +opt.val();
                      const po   = opt.data('po');
                      const art  = opt.data('article');
                      const selq = +$(this).find('.modal-selected-qty').val()||0;
                      const av   = +$(this).find('.availQty').text()||0;
                      if(pid && selq>0){
                        out.push({
                          poline_id:    pid,
                          po_number:    po,
                          article:      art,
                          available_qty:av,
                          selected_qty: selq
                        });
                      }
                    });
                    containerItems[modalContainerId]=out;
                  }
                
                  function refreshAll(){
                    // re‐enable/disable across all modal rows
                    $('#modalItemsTable tbody tr').each((_,r)=> {
                      const s = $(r).find('.modal-article');
                      s.trigger('change');
                    });
                  }
                
                  $('#save-modal-items').click(function(){
                    saveToContainerItems();
                    const sum = containerItems[modalContainerId];
                    if(!sum||!sum.length){
                      return alert("Add at least one item.");
                    }
                    const txt = sum.map(i=>`${i.po_number}–${i.article}:${i.selected_qty}`).join(', ');
                    $(`#selected-items-${modalContainerId}`).text(txt);
                    $(`#container_items_${modalContainerId}`).val(JSON.stringify(sum));
                    $('#containerItemsModal').modal('hide');
                  });
                
                  $('#dismiss-modal').click(function(){
                    $('#containerItemsModal').modal('hide');
                  });
// </script>