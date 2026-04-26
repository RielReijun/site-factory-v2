<section className="py-16 md:py-20 bg-muted/30">
  <div className="container mx-auto px-4 max-w-xl">
    <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">[[ headline ]]</h2>
    <div className="space-y-6">
      [[ ?phone ]]<div className="flex items-start gap-3"><Phone className="w-5 h-5 text-primary mt-0.5" /><div><div className="font-medium">Telefoon</div><a href="tel:[[ phone ]]" className="text-muted-foreground hover:text-primary">[[ phone ]]</a></div></div>[[ / ]]
      [[ ?email ]]<div className="flex items-start gap-3"><Mail className="w-5 h-5 text-primary mt-0.5" /><div><div className="font-medium">E-mail</div><a href="mailto:[[ email ]]" className="text-muted-foreground hover:text-primary">[[ email ]]</a></div></div>[[ / ]]
      [[ ?address ]]<div className="flex items-start gap-3"><MapPin className="w-5 h-5 text-primary mt-0.5" /><div><div className="font-medium">Adres</div><p className="text-muted-foreground">[[ address ]]</p></div></div>[[ / ]]
    </div>
  </div>
</section>