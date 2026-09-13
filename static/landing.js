(function(){
  fetch("/api/me", { credentials: "same-origin" })
    .then(function(r){ return r.ok ? r.json() : { authenticated: false }; })
    .then(function(d){
      if (!d || !d.authenticated) return;
      document.querySelectorAll('a[href="/auth/steam/login"]').forEach(function(a){
        a.setAttribute("href", "/app");
      });
      var steamBtn = document.querySelector(".btn-steam");
      if (steamBtn) {
        steamBtn.classList.remove("btn-steam");
        steamBtn.classList.add("btn", "btn-primary");
        steamBtn.innerHTML = "Continue to App &rarr;";
      }
    })
    .catch(function(){});
})();

var names = ["aadal","·en·","Kowalski","dust2god","Nova_","ripstik","J4CKAL","Meridian"];
var servers = ["Rusty Moose","Stevious 2x Mondays","Facepunch 15","Atlas EU Monthly","Survivors.gg #3"];
var ni = 0;
setInterval(function(){
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  ni = (ni + 1) % names.length;
  var nameEl = document.getElementById("tick-name");
  var srvEl = document.getElementById("tick-server");
  if (!nameEl || !srvEl) return;
  nameEl.textContent = names[ni];
  srvEl.textContent = servers[ni % servers.length];
}, 3200);
