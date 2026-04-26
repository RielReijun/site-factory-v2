<section className="py-14 bg-primary text-primary-foreground">
  <div className="container mx-auto px-4">
    [[ ?headline ]]<h2 className="font-heading text-2xl font-bold text-center mb-8">[[ headline ]]</h2>[[ / ]]
    <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
      [[ *items ]]<div className="text-center"><div className="font-heading text-4xl md:text-5xl font-bold">[[ .value ]]</div><div className="text-primary-foreground/75 text-sm mt-1">[[ .label ]]</div></div>[[ / ]]
    </div>
  </div>
</section>