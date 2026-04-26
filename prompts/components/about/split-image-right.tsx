<section className="py-16 md:py-20 bg-muted/30">
  <div className="container mx-auto px-4">
    <div className="flex flex-col md:flex-row items-center gap-10 md:gap-16">
      <div className="flex-1">
        <h2 className="font-heading text-3xl md:text-4xl font-bold mb-4">[[ headline ]]</h2>
        <p className="text-muted-foreground leading-relaxed">[[ body ]]</p>
        [[ ?bullets ]]<ul className="space-y-2 mt-4">[[ *bullets ]]<li className="flex items-start gap-2 text-sm text-muted-foreground"><Check className="w-5 h-5 text-primary mt-0.5 shrink-0" /><span>[[ .value ]]</span></li>[[ / ]]</ul>[[ / ]]
        [[ ?cta_text ]]<Link href="[[ cta_link ]]" className="inline-flex items-center gap-2 text-primary font-semibold hover:underline mt-5"><span>[[ cta_text ]]</span><ArrowRight className="w-4 h-4" /></Link>[[ / ]]
      </div>
      [[ ?image ]]<div className="w-full md:w-1/2"><img src="/[[ image ]]" alt="[[ headline ]]" className="w-full h-72 md:h-full object-cover rounded-xl" /></div>[[ / ]]
    </div>
  </div>
</section>