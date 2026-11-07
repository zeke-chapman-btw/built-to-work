document.addEventListener('click',function(event){const control=event.target.closest('[data-report-print]');if(control){event.preventDefault();window.print();}});
