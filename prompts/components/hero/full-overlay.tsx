<section className="relative min-h-[85vh] flex items-center overflow-hidden bg-gray-900">
  [[ ?image ]]<div className="absolute inset-0 bg-cover bg-center bg-no-repeat" style={{ backgroundImage: 'url(/[[ image ]])' }} />[[ / ]]
  [[ ?image ]]<div className="absolute inset-0 bg-black/55" />[[ / ]]
  [[ ?image ]][[ / ]]<div className="absolute inset-0 bg-gradient-to-br from-gray-800 to-gray-900" />
  <div className="relative z-10 container mx-auto px-4 py-24">
    <div className="max-w-2xl">
      <h1 className="font-heading text-4xl md:text-5xl lg:text-6xl font-bold text-white leading-tight mb-5">[[ headline ]]</h1>
      [[ ?subline ]]<p className="text-white/80 text-lg md:text-xl leading-relaxed mb-8 max-w-xl">[[ subline ]]</p>[[ / ]]
      <div className="flex flex-col sm:flex-row gap-3">
        <Link href="[[ cta_primary_link ]]" className="inline-flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-7 py-3.5 rounded-sm transition-colors">
          [[ cta_primary_text ]] <ArrowRight className="w-4 h-4" />
        </Link>
        [[ ?cta_secondary_text ]]<Link href="[[ cta_secondary_link ]]" className="inline-flex items-center justify-center gap-2 border-2 border-white/60 text-white hover:border-white hover:bg-white/10 font-semibold px-7 py-3.5 rounded-sm transition-all">
          [[ cta_secondary_text ]] <ArrowRight className="w-4 h-4" />
        </Link>[[ / ]]
      </div>
    </div>
  </div>
</section>
