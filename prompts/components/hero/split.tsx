<section className="py-16 md:py-24 bg-background">
  <div className="container mx-auto px-4">
    <div className="flex flex-col md:flex-row items-center gap-12">
      <div className="flex-1">
        <h1 className="font-heading text-4xl md:text-5xl font-bold leading-tight mb-5">[[ headline ]]</h1>
        [[ ?subline ]]<p className="text-muted-foreground text-lg leading-relaxed mb-8">[[ subline ]]</p>[[ / ]]
        <div className="flex flex-col sm:flex-row gap-3">
          <Link href="[[ cta_primary_link ]]" className="inline-flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-6 py-3 rounded-sm transition-colors">[[ cta_primary_text ]] <ArrowRight className="w-4 h-4" /></Link>
          [[ ?cta_secondary_text ]]<Link href="[[ cta_secondary_link ]]" className="inline-flex items-center gap-2 border border-border hover:border-primary text-foreground font-semibold px-6 py-3 rounded-sm transition-colors">[[ cta_secondary_text ]]</Link>[[ / ]]
        </div>
      </div>
      [[ ?image ]]<div className="flex-1"><img src="/[[ image ]]" alt="[[ headline ]]" className="w-full h-80 md:h-96 object-cover rounded-xl shadow-lg" /></div>[[ / ]]
    </div>
  </div>
</section>