class Scene {
public:
    Scene() : root_(new Node("root")) {}
    ~Scene() {
        for (size_t i = 0; i < meshes_.size(); ++i) delete meshes_[i];
        delete root_;
    }
    void add(Mesh* mesh) { meshes_.push_back(mesh); }  // takes ownership
    Node* root() const { return root_; }               // non-owning view

private:
    Scene(const Scene&);
    Scene& operator=(const Scene&);
    Node* root_;
    std::vector<Mesh*> meshes_;
};
