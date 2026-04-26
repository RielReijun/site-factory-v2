<section className="py-16 md:py-20 bg-background">
  <div className="container mx-auto px-4">
    [[ ?headline ]]<div className="text-center mb-12"><h2 className="font-heading text-3xl md:text-4xl font-bold mb-3">[[ headline ]]</h2>[[ ?intro ]]<p className="text-muted-foreground text-lg max-w-2xl mx-auto">[[ intro ]]</p>[[ / ]]</div>[[ / ]]
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
      [[ *items ]]<div className="bg-card border border-border rounded-xl p-5 hover:shadow-md transition-shadow">
        [[ ?.icon ]]<[[ ~.icon ]] className="w-7 h-7 text-primary mb-3" />[[ / ]]
        <h3 className="font-heading font-semibold mb-1">[[ .title ]]</h3>
        <p className="text-muted-foreground text-sm">[[ .description ]]</p>
        [[ ?.price ]]<p className="text-sm font-semibold text-primary mt-2">[[ .price ]]</p>[[ / ]]
      </div>[[ / ]]
    </div>
  </div>
</section>