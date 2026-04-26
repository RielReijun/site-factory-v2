<section className="py-16 md:py-20 bg-primary text-primary-foreground">
  <div className="container mx-auto px-4 text-center">
    <h2 className="font-heading text-3xl md:text-4xl font-bold mb-3">[[ headline ]]</h2>
    [[ ?body ]]<p className="text-primary-foreground/80 text-lg mb-8 max-w-xl mx-auto">[[ body ]]</p>[[ / ]]
    <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
      <Link href="[[ cta_link ]]" className="inline-flex items-center gap-2 bg-white text-primary hover:bg-white/90 font-semibold px-7 py-3.5 rounded-sm transition-colors">[[ cta_text ]] <ArrowRight className="w-4 h-4" /></Link>
      [[ ?phone ]]<a href="tel:[[ phone ]]" className="inline-flex items-center gap-2 text-primary-foreground/80 hover:text-primary-foreground"><Phone className="w-4 h-4" />[[ phone ]]</a>[[ / ]]
      [[ ?whatsapp ]]<a href="https://wa.me/[[ whatsapp ]]" target="_blank" rel="noopener" className="inline-flex items-center gap-2 text-primary-foreground/80 hover:text-primary-foreground"><MessageCircle className="w-4 h-4" />WhatsApp</a>[[ / ]]
    </div>
  </div>
</section>