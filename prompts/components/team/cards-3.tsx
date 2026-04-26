<section className="py-16 md:py-20 bg-background">
  <div className="container mx-auto px-4">
    <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-12">[[ headline ]]</h2>
    <div className="grid grid-cols-2 md:grid-cols-3 gap-8">
      [[ *members ]]<div className="text-center">
        [[ ?.photo ]]<img src="/[[ .photo ]]" alt="[[ .name ]]" className="w-32 h-32 rounded-full object-cover mx-auto mb-4" />[[ / ]]
        <h3 className="font-heading font-semibold text-lg">[[ .name ]]</h3>
        <p className="text-primary text-sm font-medium">[[ .role ]]</p>
        [[ ?.bio ]]<p className="text-muted-foreground text-sm mt-1">[[ .bio ]]</p>[[ / ]]
      </div>[[ / ]]
    </div>
  </div>
</section>