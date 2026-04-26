<section className="py-16 md:py-20 bg-muted/30">
  <div className="container mx-auto px-4">
    <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-12">[[ headline ]]</h2>
    <div className="flex flex-col md:flex-row gap-12 max-w-5xl mx-auto">
      <div className="flex-1 space-y-6">
        [[ ?phone ]]<div className="flex items-start gap-3"><Phone className="w-5 h-5 text-primary mt-0.5 shrink-0" /><div><div className="font-medium">Telefoon</div><a href="tel:[[ phone ]]" className="text-muted-foreground hover:text-primary">[[ phone ]]</a></div></div>[[ / ]]
        [[ ?email ]]<div className="flex items-start gap-3"><Mail className="w-5 h-5 text-primary mt-0.5 shrink-0" /><div><div className="font-medium">E-mail</div><a href="mailto:[[ email ]]" className="text-muted-foreground hover:text-primary">[[ email ]]</a></div></div>[[ / ]]
        [[ ?address ]]<div className="flex items-start gap-3"><MapPin className="w-5 h-5 text-primary mt-0.5 shrink-0" /><div><div className="font-medium">Adres</div><p className="text-muted-foreground">[[ address ]]</p></div></div>[[ / ]]
        [[ ?address ]]<LeafletMap address="[[ address ]]" />[[ / ]]
      </div>
      <div className="flex-1"><form action="https://formspree.io/f/FORMSPREE_ID" method="POST" className="space-y-4"><input type="hidden" name="_subject" value="Nieuw bericht via website" /><input type="text" name="_gotcha" style={{ display: "none" }} /><div><label className="block text-sm font-medium mb-1.5">Naam</label><input type="text" name="name" required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" /></div><div><label className="block text-sm font-medium mb-1.5">E-mail</label><input type="email" name="email" required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" /></div><div><label className="block text-sm font-medium mb-1.5">Bericht</label><textarea name="message" rows={5} required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none" /></div><button type="submit" className="w-full bg-primary hover:bg-primary/90 text-primary-foreground font-semibold py-2.5 rounded-md transition-colors">Verstuur bericht</button></form></div>
    </div>
  </div>
</section>