function setFilterMode(list){const text=document.querySelector('#text-filters'),known=document.querySelector('#list-filters');text.hidden=list;known.hidden=!list;text.querySelectorAll('input').forEach(value=>value.disabled=list);known.querySelectorAll('select').forEach(value=>value.disabled=!list)}
document.querySelectorAll('[name=filter_mode]').forEach(radio=>radio.addEventListener('change',()=>{if(radio.checked)setFilterMode(radio.value==='list')}));
const selected=document.querySelector('[name=filter_mode]:checked');if(selected)setFilterMode(selected.value==='list');
