<section className="py-16 md:py-20 bg-muted/30">
  <div className="container mx-auto px-4">
    [[ ?headline ]]<h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">[[ headline ]]</h2>[[ / ]]
    <GalleryCarousel images={[
      [[ *images ]]{ src: "/[[ .src ]]", alt: "[[ .alt ]]" },[[ / ]]
    ]} />
  </div>
</section>