<section className="py-24 md:py-32 bg-gradient-to-br from-primary/10 via-background to-background">
  <div className="container mx-auto px-4 text-center">
    <h1 className="font-heading text-4xl md:text-6xl font-bold leading-tight mb-5 max-w-3xl mx-auto">[[ headline ]]</h1>
    [[ ?subline ]]<p className="text-muted-foreground text-lg md:text-xl leading-relaxed mb-8 max-w-2xl mx-auto">[[ subline ]]</p>[[ / ]]
    <div className="flex flex-col sm:flex-row gap-3 justify-center">
      <Link href="[[ cta_primary_link ]]" className="inline-flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-7 py-3.5 rounded-sm transition-colors">[[ cta_primary_text ]] <ArrowRight className="w-4 h-4" /></Link>
      [[ ?cta_secondary_text ]]<Link href="[[ cta_secondary_link ]]" className="inline-flex items-center gap-2 border border-primary text-primary hover:bg-primary/10 font-semibold px-7 py-3.5 rounded-sm transition-colors">[[ cta_secondary_text ]]</Link>[[ / ]]
    </div>
  </div>
</section>